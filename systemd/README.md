# Systemd Unit Files

## Installation

The bot service includes both the Telegram bot and the OAuth web server (they run in the same process).

### Setup Steps

1. **Copy the service file:**
   ```bash
   sudo cp systemd/telegram-bot.service /etc/systemd/system/
   ```

2. **Edit the service file if needed:**
   ```bash
   sudo nano /etc/systemd/system/telegram-bot.service
   ```
   
   Update the following paths if your installation is in a different location:
   - `User=ubuntu` (change to your user)
   - `WorkingDirectory=/home/ubuntu/laughing-memory`
   - `EnvironmentFile=/home/ubuntu/laughing-memory/.env`
   - `ExecStart=/usr/bin/python3 /home/ubuntu/laughing-memory/bot.py`
   - `ReadWritePaths=/home/ubuntu/laughing-memory/data`

3. **Reload systemd:**
   ```bash
   sudo systemctl daemon-reload
   ```

4. **Enable and start the service:**
   ```bash
   sudo systemctl enable telegram-bot.service
   sudo systemctl start telegram-bot.service
   ```

5. **Check status:**
   ```bash
   sudo systemctl status telegram-bot.service
   ```

6. **View logs:**
   ```bash
   sudo journalctl -u telegram-bot.service -f
   ```

## Service Management

- **Start:** `sudo systemctl start telegram-bot.service`
- **Stop:** `sudo systemctl stop telegram-bot.service`
- **Restart:** `sudo systemctl restart telegram-bot.service`
- **Enable autostart:** `sudo systemctl enable telegram-bot.service`
- **Disable autostart:** `sudo systemctl disable telegram-bot.service`

## Environment Variables

Make sure your `.env` file contains all required variables:
- `BOT_TOKEN`
- `ALLOWED_USER_IDS`
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `PUBLIC_BASE_URL`
- `OAUTH_SERVER_HOST=127.0.0.1`
- `OAUTH_SERVER_PORT=8000`

See `.env.example` for a complete list of available configuration options.
