# Running Flask Backup Receiver in Background

This guide explains how to run the Flask backup receiver application in the background on Windows.

## Option 1: Simple Background Script (Recommended for Testing)

### Start the app in background:
Double-click `start_background.bat` or run it from command prompt.

The app will run in the background without showing a console window.

### Stop the app:
Double-click `stop_background.bat` or run it from command prompt.

This will find and kill any running instances of the Flask app.

## Option 2: Windows Service (Recommended for Production)

### Prerequisites:
1. Install pywin32:
   ```bash
   pip install pywin32
   ```

### Install the service:
```bash
python run_as_service.py install
```

### Start the service:
```bash
python run_as_service.py start
```

Or use Windows Services Manager:
1. Press `Win + R`, type `services.msc`, press Enter
2. Find "Flask Backup Receiver Service"
3. Right-click and select "Start"

### Stop the service:
```bash
python run_as_service.py stop
```

Or use Windows Services Manager to stop it.

### Remove the service:
```bash
python run_as_service.py remove
```

### Service Management:
- The service will automatically start on Windows boot (if set to Automatic)
- Logs are written to `logs/receiver.log`
- Check service status in Windows Services Manager

## Logs

All application logs are written to:
- `logs/receiver.log` - Application log file
- Console output (when running in foreground)

## Checking if the app is running:

### Method 1: Check port
```bash
netstat -ano | findstr :5001
```

### Method 2: Check processes
```bash
tasklist | findstr python
```

### Method 3: Test the API
Open browser and go to: `http://localhost:5001/progress/test` (should return 404, but confirms server is running)

## Troubleshooting

### Port already in use:
If port 5001 is already in use, you can:
1. Stop the existing process using `stop_background.bat`
2. Or change the port in `receiver.py` (line 665)

### Service won't start:
- Make sure you ran the install command as Administrator
- Check Windows Event Viewer for service errors
- Verify pywin32 is installed: `pip show pywin32`

### Can't find the process:
- The app might be running under a different name
- Check all Python processes: `tasklist | findstr python`
- Kill by port: `stop_background.bat` handles this automatically

