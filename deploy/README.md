# Deploying CCXT-Fr with Docker

One image (`ccxt-freqtrade:local`, built from this repo's freqtrade checkout) runs both the control panel and
every bot. Only the panel is a compose service; it creates one container per bot (`ccxt-bot-<name>`) when you press
"start" on the panel, and stops/starts it on demand. All containers use host networking and bind to 127.0.0.1 —
nothing is exposed publicly.

## 1. Server (Ubuntu 24.04)

```bash
curl -fsSL https://get.docker.com | sudo sh          # Docker Engine + compose plugin
sudo usermod -aG docker $USER                          # log out/in afterwards
sudo mkdir -p /opt/ccxt && sudo chown $USER /opt/ccxt
```

## 2. Copy the code (from the Mac)

```bash
# freqtrade checkout, without the local venv, git data and market data
rsync -a --delete --exclude .venv --exclude .git --exclude 'user_data/*' --exclude external \
  ~/Documents/07-Hero/CCXT-Fr/ SERVER:/opt/ccxt/
# user_data: code and configs (the git-tracked part) + the credentials file, no market data
cd ~/Documents/07-Hero/CCXT-Fr/user_data && git ls-files -z | rsync -a --from0 --files-from=- ./ SERVER:/opt/ccxt/user_data/
rsync -a bots/secrets.json SERVER:/opt/ccxt/user_data/bots/
# optional: carry over the dry-run records
rsync -a tradesv3.*.sqlite SERVER:/opt/ccxt/user_data/
```

On the server, `user_data/bots/local-network.json` must be `{}` (the Binance mirror override is only for the Mac):

```bash
echo '{}' > /opt/ccxt/user_data/bots/local-network.json
sudo chown -R 1000:1000 /opt/ccxt/user_data             # bot containers run as uid 1000 (ftuser)
```

## 3. Build and start

```bash
cd /opt/ccxt/user_data/deploy
export CCXT_USER_DATA=/opt/ccxt/user_data
docker compose up -d --build                            # builds the image, starts the panel
docker compose logs -f panel                            # Ctrl-C to leave
```

Bots whose state was "running" are brought up by the panel's supervisor; others are started from the page.
Data candles: freqtrade downloads what each strategy needs on start (startup candles), no bulk data needed.

## 4. Open the panel from the Mac

```bash
ssh -N -L 8090:127.0.0.1:8090 -L 8089:127.0.0.1:8089 \
  -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 -L 8082:127.0.0.1:8082 -L 8083:127.0.0.1:8083 \
  -L 8084:127.0.0.1:8084 -L 8085:127.0.0.1:8085 -L 8086:127.0.0.1:8086 SERVER
```

Then http://127.0.0.1:8090 (panel) and http://127.0.0.1:8089 (FreqUI). FreqUI calls the bot ports directly from
the browser, hence the extra forwards. Stop the Mac's own panel first (`launchctl bootout gui/$(id -u)/com.ccxtfr.panel`)
so the local ports are free.

## Updating

```bash
# after rsync of new code:
cd /opt/ccxt/user_data/deploy && docker compose up -d --build
# bot containers pick up the new image the next time they are started from the panel
# (the panel recreates a bot container whenever its image or command changed)
```

## Tests (no Docker needed)

`user_data/panel/test_docker_runner.py` checks container create/start/stop/inspect and path translation against a
fake Docker API.
