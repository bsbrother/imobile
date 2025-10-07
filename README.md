# iMobile - Stock Portfolio Tracking & Mobile Automation

A comprehensive web application built with [Reflex](https://reflex.dev/) that combines stock portfolio tracking with mobile automation capabilities.

## Features

### Stock Portfolio Tracking
- **Real-time Portfolio View**: Track your stock investments with live data
- **Performance Metrics**: Monitor daily P&L, cumulative returns, and floating profits
- **Multi-stock Management**: View all your holdings in one place
- **Responsive Design**: Works seamlessly on desktop, tablet, and mobile devices
- **SQLite Database**: All data persisted in a local database for privacy and speed

### Mobile Automation (Android)
adb supports basic automation capabilities like touch or keypress. like AndroidViewClient or appium or UiAutomator are used for pyautogui-like automation.

## TODO:
- https://dev.to/zhanghandong/agentkit-a-technical-vision-for-building-universal-ai-automation-for-human-computer-interaction-2523
  Implement a universal AI automation kit that not only supports Android phones but also iOS, desktop platforms, and even any smart terminal.
- [No LLM](See ~/webos/docs/no-LLM-mobile.md)

## Setup

### Portfolio Tracking Setup

1. **Install dependencies**:
   ```bash
   # Using UV (recommended)
   uv venv --python 3.12
   source .venv/bin/activate
   pip install reflex
   ```

2. **Initialize the database**:
   ```bash
   # Import the database schema and sample data
   sqlite3 imobile.db < db/imobile.sql
   
   # Or initialize Reflex database
   reflex db init
   reflex db migrate
   ```

3. **Run the application**:
   ```bash
   reflex run
   ```

4. **Access the portfolio**:
   - Open browser: `http://localhost:3000/portfolio`
   - Demo user credentials: `demo@example.com`

### Testing Database Integration

```bash
# Run database integration test
python tests/test_portfolio_db.py
```

## Mobile Automation

### Prerequirements
- See ~/webos/genymotion-update-arm/readme.md

### With LLM

```bash
# Ensure have the genymotion installed
which gmtool
# Start the device if not already running
gmtool admin start 'Google Pixel 6 Pro'
# Ensure support arm64 on x86_64 emulator
adb shell getprop ro.product.cpu.abilist
# x86_64,x86,arm64-v8a,armeabi-v7a,armeabi

# List of available devices
gmtool admin list
# State    |   ADB Serial    |                UUID                |      Name
#       On |  127.0.0.1:6555 |6ed0145d-a1c6-43e2-9425-b4f8b38d7cb2| Google Pixel 6 Pro

# Ensure you have the Android emulator running and accessible via ADB
adb devices -l
# 127.0.0.1:6555 device product:vbox86p model:Pixel_6_Pro device:vbox86p transport_id:7

# droidrun package
git clone https://github.com/droidrun/droidrun utils/droidrun
cd utils/droidrun; git pull; pip install -U -e utils/droidrun/.

# droidrun-portal apk installed.
droidrun setup # Will auto download and installed, --path=~/Downloads/droidrun-portal.apk
Mobile ->Down Tap Up ->Settings ->Accessibility ->DroidRun Portal ->Enable
droidrun ping
Portal is installed and accessible. You're good to go!
droidrun devices
Found 1 connected device(s):
  • 127.0.0.1:6555
droidrun status
Device: 127.0.0.1:6555
  Model: Google Pixel 6 Pro
  Android Version: 13
  SDK Version: 33

# Test task
python tests/test_droidrun.py

# Install stock apk
- [Download gtht apk](http://app.gtht.com/jh-download/)
- Install apk into genymotion device(google nexus 4) as a app
adb install -r ~/Downloads/yyz_9.19.1620250524005127.19.16-25052321_gtja.apk
# Find the package name using aapt or by checking the app's manifest from apk file.
aapt dump badging ~/Downloads/yyz_9.19.1620250524005127.19.16-25052321_gtja.apk
# package: name='com.guotai.dazhihui' versionCode='25052321' versionName='9.19.16' compileSdkVersion='34' compileSdkVersionCodename='14'
# application-label:'国泰海通君弘'
adb shell ps|grep guotai
adb shell pm list packages | grep guotai
adb shell am force-stop com.guotai.dazhihui


## Fix
- Mobile pop a window, show 'System UI is not responsing ?'
  <Close app> <Wait>
  - adb devices show 'online':
    adb shell am restart com.android.systemui # adb shell pkill com.android.systemui
  - Manual any action no effect on mobile
  - adb devices show 'offline', adb kill-server; adb start-server # always offline.
    adb reconnect offline
    adb reconnect device
    adb shell am startservice -n com.android.systemui/.SystemUIService
  - Must reboot mobile to solve:
    gmtool admin stop 'Google Pixel 6 Pro'
    gmtool admin start 'Google Pixel 6'

  # Increase ADB timeout
  echo "export ADB_TIMEOUT=10000" >> ~/.bashrc
  # Clear UI cache
  adb shell pm clear com.android.systemui
  # gmtool admin factoryreset "Google Pixel 6 Pro"
  # adb shell cmd package install-existing com.android.systemui

  # In Genymotion Settings: $ genymotion
  - Increase RAM to 4096 MB
  - Change Graphics Mode → Switch between Software/Hardware rendering
  - Disable "Use virtual device framebuffer"

```

## Refresh real-time data from mobile apps

### Quick Start
```bash
python app_guotai.py
```

This script uses DroidRun to interact with the Guotai mobile app and fetch:
1. **Market Indices** - Shanghai, Shenzhen, and ChiNext index data
2. **Stock Quotes** - Real-time prices and changes for held stocks
3. **Portfolio Summary** - Total assets, market value, positions, and P&L
4. **Stock Positions** - Detailed position data including holdings and available shares

### Sync App Real-Time Data to Database
The fetched app real-time data is automatically sync to the database using the `sync_app_data_to_db()` function:

```python
from app_guotai import get_from_app_quote_page, get_from_app_position_page, sync_app_data_to_db

# Fetch data from mobile app
quote_data = get_from_app_quote_page()
position_data = get_from_app_position_page()

# Save to database
result = save_app_data_to_db(
    quote_data=quote_data,
    position_data=position_data,
    user_id=1
)

print(f"Success: {result['success']}")
print(f"Updated: {result['indices_updated']} indices, {result['stocks_updated']} stocks")
```

### Database Schema Updates

**First Time Setup:**
```bash
# reflex init
# reflex db init # default is reflex.db, change rxconfig.py, alembic.ini
# Not create alembic/, then alembic init ./alembic
# Apply db schema changes
# reflex db makemigrations -m "Initial migration"
# reflex db migrate

# 1. Initialize database with schema
sqlite3 imobile.db < db/imobile.sql

# 2. Apply migrations
alembic upgrade head

# 3. Verify migration status
alembic current
```

**Applying New Migrations:**
```bash
# Apply pending migrations
alembic upgrade head

# Verify migration status
reflex db status  # or: alembic current
```

**Rollback (if needed):**
```bash
# Rollback one migration
alembic downgrade -1
```

This adds:
- `total_table`: `position_percent`, `withdrawable`, `last_updated`
- `stocks_table`: `available_shares`, `last_updated`

📖 **Documentation**: 
- See [docs/DATABASE_MIGRATION_GUIDE.md](docs/DATABASE_MIGRATION_GUIDE.md) for migration best practices
- See [docs/REALTIME_DATA_MAPPING.md](docs/REALTIME_DATA_MAPPING.md) for detailed data mapping

## Analysis stocks
```bash
python ../illm/utils/EvoAgentX/Wonderful_workflow_corpus/invest/stock_analysis.py 000006 >/tmp/tmp 2>&1
find /tmp/000006 -type f -name '*.md' # Or html
```

