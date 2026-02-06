# Nginx Configuration

This directory contains the nginx configuration template for proxying OAuth requests to the local OAuth server.

## Installation

1. **Copy the configuration file:**
   ```bash
   sudo cp nginx/telegram-bot-oauth.conf /etc/nginx/sites-available/
   ```

2. **Edit the configuration:**
   ```bash
   sudo nano /etc/nginx/sites-available/telegram-bot-oauth.conf
   ```
   
   Update the following:
   - `server_name vanekpetrov1997.fvds.ru;` (change to your domain)
   - SSL certificate paths (if using HTTPS)
   - Uncomment SSL configuration block if you have SSL certificates

3. **Enable the site:**
   ```bash
   sudo ln -s /etc/nginx/sites-available/telegram-bot-oauth.conf /etc/nginx/sites-enabled/
   ```

4. **Test nginx configuration:**
   ```bash
   sudo nginx -t
   ```

5. **Reload nginx:**
   ```bash
   sudo systemctl reload nginx
   ```

## SSL Setup (Recommended)

For production, you should use HTTPS with Let's Encrypt:

1. **Install certbot:**
   ```bash
   sudo apt update
   sudo apt install certbot python3-certbot-nginx
   ```

2. **Obtain SSL certificate:**
   ```bash
   sudo certbot --nginx -d vanekpetrov1997.fvds.ru
   ```

3. **Certbot will automatically configure nginx for HTTPS**

4. **Test auto-renewal:**
   ```bash
   sudo certbot renew --dry-run
   ```

## Firewall Configuration

Make sure ports 80 and 443 are open:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status
```

## Verification

After setup, verify the endpoints:

1. **Health check:**
   ```bash
   curl http://your-domain/health
   # Should return: ok
   ```

2. **OAuth start (should redirect to Google):**
   ```bash
   curl -I http://your-domain/oauth2/start?state=12345
   # Should return: 302 Found with Location header
   ```

## Notes

- The OAuth server runs on `127.0.0.1:8000` and is NOT accessible from outside
- Nginx acts as a reverse proxy, exposing only `/health` and `/oauth2/*` endpoints
- All other paths return 404
- Make sure the Telegram bot service is running before testing
