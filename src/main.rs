use aes::Aes128;
use aes::cipher::{BlockDecrypt, KeyInit, generic_array::GenericArray};
use chrono::{DateTime, Utc};
use dotenvy::dotenv;
use futures::stream::StreamExt;
use std::collections::{HashMap, HashSet};
use std::env;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::time;
use bluer::{AdapterEvent, DeviceEvent, DeviceProperty, DiscoveryFilter, DiscoveryTransport};

const GOVEE_MANUFACTURER_ID: u16 = 61320; // 0xEF88
const GOVEE_ALT_ID: u16 = 60552; // 0xEC88

#[derive(Debug, Clone)]
struct SensorState {
    is_open: bool,
    last_seen: DateTime<Utc>,
    open_since: Option<DateTime<Utc>>,
    last_notified: Option<DateTime<Utc>>,
    last_packet_timestamp: u32,
}

fn calculate_govee_crc(data: &[u8]) -> u16 {
    let mut crc: u32 = 0x1D0F;
    for &b in data {
        for s in (0..8).rev() {
            let mut mask = 0;
            if ((crc >> 15) ^ ((b as u32 >> s) & 1)) != 0 {
                mask = 0x1021;
            }
            crc = ((crc << 1) ^ mask) & 0xFFFF;
        }
    }
    crc as u16
}

fn decrypt_h5123(data: &[u8]) -> Option<bool> {
    if data.len() < 24 { return None; }
    let timestamp = &data[2..6];
    let enc_data = &data[6..22];
    let enc_crc = u16::from_be_bytes([data[22], data[23]]);
    if calculate_govee_crc(enc_data) != enc_crc { return None; }
    let mut key_bytes = [0u8; 16];
    key_bytes[0..4].copy_from_slice(timestamp);
    let mut reversed_key = key_bytes;
    reversed_key.reverse();
    let mut reversed_data = [0u8; 16];
    reversed_data.copy_from_slice(enc_data);
    reversed_data.reverse();
    let key = GenericArray::from_slice(&reversed_key);
    let cipher = Aes128::new(key);
    let mut block = GenericArray::clone_from_slice(&reversed_data);
    cipher.decrypt_block(&mut block);
    let mut decrypted = [0u8; 16];
    decrypted.copy_from_slice(block.as_slice());
    decrypted.reverse();
    let model_id = decrypted[2];
    let state_byte = decrypted[5];
    if model_id == 2 {
        match state_byte {
            2 => Some(true),
            1 => Some(false),
            _ => None,
        }
    } else { None }
}

fn parse_fallback(data: &[u8]) -> Option<bool> {
    if data.len() < 24 && data.len() >= 5 {
        Some((data[4] & 0x01) == 1)
    } else { None }
}

fn parse_denylist(raw: &str) -> HashSet<String> {
    raw.split(',')
        .map(|s| s.trim().to_uppercase())
        .filter(|s| !s.is_empty())
        .collect()
}

fn apply_sensor_update(
    state: &mut SensorState,
    is_open: bool,
    packet_ts: u32,
    now: DateTime<Utc>,
    mac: &str,
) -> bool {
    let is_newer = packet_ts > state.last_packet_timestamp;
    let is_reboot = if packet_ts < state.last_packet_timestamp {
        (state.last_packet_timestamp - packet_ts) > 10000
    } else { false };
    let is_stale_timeout = (now - state.last_seen).num_seconds() > 10;

    if !is_newer && !is_reboot && !is_stale_timeout { return false; }

    state.last_seen = now;
    state.last_packet_timestamp = packet_ts;

    if state.is_open != is_open {
        println!(
            "[{}] {} state change: {} -> {} (TS: {})",
            now.format("%H:%M:%S"), mac,
            if state.is_open { "OPEN" } else { "CLOSED" },
            if is_open { "OPEN" } else { "CLOSED" },
            packet_ts
        );
        state.is_open = is_open;
        state.open_since = if is_open { Some(now) } else { None };
        if !is_open { state.last_notified = None; }
        return true;
    }
    false
}

fn send_notification(channel_id: &str, message: &str) {
    let url = format!("https://ntfy.sh/{}", channel_id);
    let client = reqwest::blocking::Client::new();
    let _ = client.post(&url).body(message.to_string()).send();
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    dotenv().ok();

    let channel_id = env::var("NTFY_CHANNEL_ID").expect("NTFY_CHANNEL_ID must be set in .env");
    let polling_interval = env::var("POLLING_INTERVAL_SECONDS").unwrap_or("1.0".into()).parse::<u64>().unwrap_or(1);
    let threshold_seconds = env::var("DOOR_OPEN_THRESHOLD_SECONDS").unwrap_or("600.0".into()).parse::<f64>().unwrap_or(600.0);
    let repeat_interval = env::var("NOTIFICATION_REPEAT_INTERVAL_SECONDS").unwrap_or("60.0".into()).parse::<u64>().unwrap_or(60);
    let denylist = Arc::new(parse_denylist(&env::var("BLUETOOTH_DENYLIST").unwrap_or_default()));

    let govee_count = Arc::new(AtomicU64::new(0));
    let govee_count_clone = govee_count.clone();

    println!("--- RUST DOOR MONITOR (DEBUG MODE) ---");
    let sensors: Arc<Mutex<HashMap<String, SensorState>>> = Arc::new(Mutex::new(HashMap::new()));
    let sensors_processor = sensors.clone();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<(String, u32, bool)>();

    let session = bluer::Session::new().await?;
    let adapter = session.default_adapter().await?;
    adapter.set_powered(true).await?;
    println!("Adapter: {} (Powered ON)", adapter.name());

    let denylist_clone = denylist.clone();
    let adapter_clone = adapter.clone();
    let tx_clone = tx.clone();
    
    tokio::spawn(async move {
        let mut seen = HashSet::new();
        let tx_spawn = tx_clone.clone();
        let govee_spawn = govee_count_clone.clone();
        let denylist_spawn = denylist_clone.clone();
        let adapter_inner = adapter_clone.clone();

        // Load pre-cached devices
        if let Ok(addrs) = adapter_inner.device_addresses().await {
            for addr in addrs {
                let mac = addr.to_string().to_uppercase();
                if denylist_spawn.contains(&mac) { continue; }
                if let Ok(device) = adapter_inner.device(addr) {
                    seen.insert(mac.clone());
                    let tx_dev = tx_spawn.clone();
                    let govee_dev = govee_spawn.clone();
                    let mac_dev = mac.clone();
                    tokio::spawn(async move {
                        println!("[DEBUG] Monitoring known sensor: {}", mac_dev);
                        let mut dev_events = device.events().await.unwrap();
                        while let Some(de) = dev_events.next().await {
                            if let DeviceEvent::PropertyChanged(prop) = de {
                                match prop {
                                    DeviceProperty::ManufacturerData(md) => {
                                        if let Some(data) = md.get(&GOVEE_MANUFACTURER_ID).or_else(|| md.get(&GOVEE_ALT_ID)) {
                                            govee_dev.fetch_add(1, Ordering::Relaxed);
                                            let packet_ts = if data.len() >= 6 {
                                                u32::from_be_bytes([data[2], data[3], data[4], data[5]])
                                            } else { 0 };
                                            if let Some(is_open) = decrypt_h5123(data).or_else(|| parse_fallback(data)) {
                                                let _ = tx_dev.send((mac_dev.clone(), packet_ts, is_open));
                                            }
                                        }
                                    },
                                    DeviceProperty::Rssi(val) => {
                                        // If we see RSSI but no manufacturer data, BlueZ is caching
                                        // println!("[DEBUG] {} RSSI: {}", mac_dev, val);
                                    },
                                    _ => {}
                                }
                            }
                        }
                    });
                }
            }
        }

        // Start discovery
        let filter = DiscoveryFilter { transport: DiscoveryTransport::Le, ..Default::default() };
        let _ = adapter_inner.set_discovery_filter(filter).await;
        let discovery_session = adapter_inner.discover_devices().await.unwrap();
        let mut events = adapter_inner.events().await.unwrap();

        println!("[DEBUG] Scanning for new devices...");

        while let Some(event) = events.next().await {
            let _keep_alive = &discovery_session;
            if let AdapterEvent::DeviceAdded(addr) = event {
                println!("[DEBUG] New device discovered: {}", addr);
                let mac = addr.to_string().to_uppercase();
                if denylist_spawn.contains(&mac) || seen.contains(&mac) { continue; }
                if let Ok(device) = adapter_inner.device(addr) {
                    seen.insert(mac.clone());
                    println!("[DEBUG] New sensor discovered: {}", mac);
                    let tx_new = tx_spawn.clone();
                    let govee_new = govee_spawn.clone();
                    let mac_new = mac.clone();
                    tokio::spawn(async move {
                        let mut dev_events = device.events().await.unwrap();
                        while let Some(de) = dev_events.next().await {
                            if let DeviceEvent::PropertyChanged(DeviceProperty::ManufacturerData(md)) = de {
                                if let Some(data) = md.get(&GOVEE_MANUFACTURER_ID).or_else(|| md.get(&GOVEE_ALT_ID)) {
                                    govee_new.fetch_add(1, Ordering::Relaxed);
                                    let packet_ts = if data.len() >= 6 {
                                        u32::from_be_bytes([data[2], data[3], data[4], data[5]])
                                    } else { 0 };
                                    if let Some(is_open) = decrypt_h5123(data).or_else(|| parse_fallback(data)) {
                                        let _ = tx_new.send((mac_new.clone(), packet_ts, is_open));
                                    }
                                }
                            }
                        }
                    });
                }
            }
        }
    });

    // Processor Task
    tokio::spawn(async move {
        let mut event_buffer = Vec::new();
        loop {
            let timeout = time::sleep(Duration::from_millis(500));
            tokio::select! {
                Some(event) = rx.recv() => { event_buffer.push(event); }
                _ = timeout => {
                    if event_buffer.is_empty() { continue; }
                    event_buffer.sort_by_key(|e| e.1);
                    let mut map = sensors_processor.lock().unwrap();
                    let now = Utc::now();
                    for (mac, packet_ts, is_open) in event_buffer.drain(..) {
                        let entry = map.entry(mac.clone()).or_insert_with(|| {
                            println!("[{}] Discovered: {}", now.format("%H:%M:%S"), mac);
                            SensorState {
                                is_open, last_seen: now, open_since: if is_open { Some(now) } else { None },
                                last_notified: None, last_packet_timestamp: packet_ts,
                            }
                        });
                        apply_sensor_update(entry, is_open, packet_ts, now, &mac);
                    }
                }
            }
        }
    });

    let mut interval = time::interval(Duration::from_secs(polling_interval));
    let mut last_heartbeat = Utc::now();

    loop {
        interval.tick().await;
        let now = Utc::now();
        if (now - last_heartbeat).num_seconds() >= 60 {
            println!("[{}] HB: Govee Packets: {}", now.format("%H:%M:%S"), govee_count.load(Ordering::Relaxed));
            last_heartbeat = now;
        }
        let mut map = sensors.lock().unwrap();
        for (mac, state) in map.iter_mut() {
            if state.is_open {
                if let Some(open_since) = state.open_since {
                    let duration = now - open_since;
                    if duration >= chrono::Duration::seconds(threshold_seconds as i64) {
                        let should_notify = match state.last_notified {
                            Some(last) => (now - last).num_seconds() >= repeat_interval as i64,
                            None => true,
                        };
                        if should_notify {
                            let duration_str = if duration.num_minutes() > 0 {
                                format!("{} mins", duration.num_minutes())
                            } else {
                                format!("{} secs", duration.num_seconds())
                            };
                            let message = format!("Door Sensor {} open for {}!", mac, duration_str);
                            send_notification(&channel_id, &message);
                            state.last_notified = Some(now);
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_apply_sensor_update_ordering() {
        let mut state = SensorState {
            is_open: false, last_seen: Utc::now(), open_since: None, last_notified: None,
            last_packet_timestamp: 1000,
        };
        let now = Utc::now();
        let changed = apply_sensor_update(&mut state, true, 1100, now, "TEST");
        assert!(changed);
        assert!(state.is_open);
    }
}
