use aes::Aes128;
use aes::cipher::{BlockDecrypt, KeyInit, generic_array::GenericArray};
use btleplug::api::{Central, Manager as _, Peripheral, ScanFilter};
use btleplug::platform::Manager;
use chrono::{DateTime, Utc};
use dotenvy::dotenv;
use futures::stream::StreamExt;
use std::collections::{HashMap, HashSet};
use std::env;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::time;

const GOVEE_MANUFACTURER_ID: u16 = 61320; // 0xEF88

#[derive(Debug, Clone)]
struct SensorState {
    is_open: bool,
    last_seen: DateTime<Utc>,
    open_since: Option<DateTime<Utc>>,
    last_notified: Option<DateTime<Utc>>,
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

/// Decrypts H5123 manufacturer data (24-byte packet)
fn decrypt_h5123(data: &[u8]) -> Option<bool> {
    if data.len() < 24 {
        return None;
    }

    let timestamp = &data[2..6];
    let enc_data = &data[6..22];
    let enc_crc = u16::from_be_bytes([data[22], data[23]]);
    
    // 1. Verify CRC
    if calculate_govee_crc(enc_data) != enc_crc {
        return None;
    }

    // 2. Build Key (Timestamp + 12 zeros)
    let mut key_bytes = [0u8; 16];
    key_bytes[0..4].copy_from_slice(timestamp);
    
    // Govee reverses everything: key and data
    let mut reversed_key = key_bytes;
    reversed_key.reverse();
    
    let mut reversed_data = [0u8; 16];
    reversed_data.copy_from_slice(enc_data);
    reversed_data.reverse();
    
    // 3. Decrypt (AES-128-ECB)
    let key = GenericArray::from_slice(&reversed_key);
    let cipher = Aes128::new(key);
    let mut block = GenericArray::clone_from_slice(&reversed_data);
    cipher.decrypt_block(&mut block);

    // 4. Reverse result back
    let mut decrypted = [0u8; 16];
    decrypted.copy_from_slice(block.as_slice());
    decrypted.reverse();

    // Bytes: [0x01, 0x05, model_id, 0x02, battery, state, ...]
    let model_id = decrypted[2];
    let state_byte = decrypted[5];
    
    if model_id == 2 {
        match state_byte {
            2 => Some(true),
            1 => Some(false),
            _ => None,
        }
    } else {
        None
    }
}

/// Fallback for unencrypted packets (based on your earlier manual analysis)
fn parse_fallback(data: &[u8]) -> Option<bool> {
    // Only use fallback for short packets (usually 7 bytes for unencrypted H5123)
    // Encrypted H5123 packets are 24 bytes; if decryption failed, data[4] is random/encrypted.
    if data.len() < 24 && data.len() >= 5 {
        // We found bit 0 of index 4 was the state indicator
        Some((data[4] & 0x01) == 1)
    } else {
        None
    }
}

fn send_notification(channel_id: &str, message: &str) {
// ... (rest of the file stays same until main event loop)
    let url = format!("https://ntfy.sh/{}", channel_id);
    let client = reqwest::blocking::Client::new();
    match client.post(&url).body(message.to_string()).send() {
        Ok(res) => {
            if res.status().is_success() {
                println!("[{}] Notification sent: {}", Utc::now().format("%H:%M:%S"), message);
            } else {
                println!("[{}] Failed to send notification: status {}", Utc::now().format("%H:%M:%S"), res.status());
            }
        }
        Err(e) => println!("[{}] Error sending notification: {}", Utc::now().format("%H:%M:%S"), e),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    dotenv().ok();

    let channel_id = env::var("NTFY_CHANNEL_ID").expect("NTFY_CHANNEL_ID must be set in .env");
    let polling_interval = env::var("POLLING_INTERVAL_SECONDS").unwrap_or("1.0".into()).parse::<u64>().unwrap_or(1);
    let threshold_minutes = env::var("DOOR_OPEN_THRESHOLD_MINUTES").unwrap_or("10.0".into()).parse::<f64>().unwrap_or(10.0);
    let repeat_interval = env::var("NOTIFICATION_REPEAT_INTERVAL_SECONDS").unwrap_or("60.0".into()).parse::<u64>().unwrap_or(60);

    println!("--- RUST DOOR MONITOR ---");
    println!("Channel ID: {}", channel_id);
    println!("Threshold: {} min", threshold_minutes);

    let sensors: Arc<Mutex<HashMap<String, SensorState>>> = Arc::new(Mutex::new(HashMap::new()));
    let sensors_clone = sensors.clone();

    // 1. Setup Bluetooth
    let manager = Manager::new().await?;
    let adapters = manager.adapters().await?;
    let central = adapters.into_iter().next().ok_or("No Bluetooth adapters found")?;

    // 2. Start Scanning
    central.start_scan(ScanFilter::default()).await?;
    let mut events = central.events().await?;

    println!("Scanner started. Watching for Govee sensors...");

    // 3. Background Event Loop (Parser)
    let central_clone = central.clone();
    let mut ignored_devices = HashSet::new();
    let mut govee_devices = HashSet::new();

    tokio::spawn(async move {
        while let Some(event) = events.next().await {
            let (id, manufacturer_data): (btleplug::platform::PeripheralId, Option<HashMap<u16, Vec<u8>>>) = match event {
                btleplug::api::CentralEvent::ManufacturerDataAdvertisement { id, manufacturer_data } => {
                    (id, Some(manufacturer_data))
                }
                btleplug::api::CentralEvent::DeviceUpdated(id) => {
                    if ignored_devices.contains(&id) {
                        continue;
                    }

                    // On some Linux systems, property changes are better caught here
                    if let Ok(peripheral) = central_clone.peripheral(&id).await {
                        if let Ok(Some(props)) = peripheral.properties().await {
                            let is_govee = props.manufacturer_data.contains_key(&GOVEE_MANUFACTURER_ID);
                            
                            if !is_govee && !govee_devices.contains(&id) {
                                ignored_devices.insert(id.clone());
                                continue;
                            }
                            
                            if is_govee {
                                govee_devices.insert(id.clone());
                            }

                            (id, Some(props.manufacturer_data))
                        } else {
                            continue;
                        }
                    } else {
                        continue;
                    }
                }
                _ => continue,
            };

            if let Some(data_map) = manufacturer_data {
                if let Some(data) = data_map.get(&GOVEE_MANUFACTURER_ID) {
                    println!("[DIAGNOSTIC] Govee signal: ID={:?}, data={:02X?}", id, data);
                    // Try Decryption first, then Fallback
                    let state = decrypt_h5123(data).or_else(|| parse_fallback(data));

                    if let Some(is_open) = state {
                        let mac = id.to_string();
                        let mut map = sensors_clone.lock().unwrap();
                        let now = Utc::now();

                        let entry = map.entry(mac.clone()).or_insert_with(|| {
                            println!("[{}] Discovered sensor: {}", now.format("%H:%M:%S"), mac);
                            SensorState {
                                is_open,
                                last_seen: now,
                                open_since: if is_open { Some(now) } else { None },
                                last_notified: None,
                            }
                        });

                        // Handle state changes
                        if entry.is_open != is_open {
                            println!("[{}] {} state change: {} -> {}", now.format("%H:%M:%S"), mac, if entry.is_open { "OPEN" } else { "CLOSED" }, if is_open { "OPEN" } else { "CLOSED" });
                            entry.is_open = is_open;
                            if is_open {
                                entry.open_since = Some(now);
                            } else {
                                entry.open_since = None;
                                entry.last_notified = None;
                            }
                        }
                        
                        // Update last seen regardless of state change
                        entry.last_seen = now;
                    }
                }
            }
        }
    });

    // 4. Main Polling Loop (Monitor/Notifier)
    let threshold_duration = chrono::Duration::seconds((threshold_minutes * 60.0) as i64);
    let mut interval = time::interval(Duration::from_secs(polling_interval));

    loop {
        interval.tick().await;
        let now = Utc::now();
        let mut map = sensors.lock().unwrap();

        for (mac, state) in map.iter_mut() {
            if state.is_open {
                if let Some(open_since) = state.open_since {
                    let duration = now - open_since;
                    if duration >= threshold_duration {
                        let should_notify = match state.last_notified {
                            Some(last) => (now - last).num_seconds() >= repeat_interval as i64,
                            None => true,
                        };

                        if should_notify {
                            let minutes = duration.num_minutes();
                            let message = format!("Door Sensor {} has been open for {} minutes!", mac, minutes);
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
    fn test_decrypt_h5123_open() {
        // Sample from user: 13 b6 03 f3 3b ... (Open)
        let data = hex::decode("13b603f33bbd8bc589b8e2c54172a7ca6e25bfbdfca58cac").unwrap();
        assert_eq!(decrypt_h5123(&data), Some(true));
    }

    #[test]
    fn test_decrypt_h5123_closed() {
        // Sample from user: 13 b6 03 f3 d3 ... (Closed)
        let data = hex::decode("13b603f3d3b1e19b34632fd464b3e9c3768346fae9e9f349").unwrap();
        assert_eq!(decrypt_h5123(&data), Some(false));
    }

    #[test]
    fn test_parse_fallback_open() {
        // index 4 is 0x01 (Open)
        let data = [0x13, 0xb6, 0x03, 0xff, 0x01];
        assert_eq!(parse_fallback(&data), Some(true));
    }

    #[test]
    fn test_parse_fallback_closed() {
        // index 4 is 0x00 (Closed)
        let data = [0x13, 0xb6, 0x03, 0xff, 0x00];
        assert_eq!(parse_fallback(&data), Some(false));
    }
}
