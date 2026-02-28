
Task:

Create a python command line application that uses Bleak library to poll a number of door sensor bluetooth devices identified by MAC addresses in a configuration file.

The application will poll for sensors on a regular interval (specified by config file, default 1 second)

If it detects door closure, all good.

Leave the exact door closure logic unimplemented, I am still trying to learn the exact data in the message

If it detects door opening, it starts measuring time of how long the door was open.

When the time exceeds 10 minutes, it uses ntfy.sh service to send a notification to a channel (channel id configured in config file). It keeps sending such notifications until the door is closed.

Notification example via curl is curl -d "Hi" ntfy.sh/cb87bdee-31d8-41fd-aca6-f729d28ae8ef


