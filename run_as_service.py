"""
Windows Service wrapper for Flask backup receiver.
Requires: pip install pywin32

To install service: python run_as_service.py install
To start service: python run_as_service.py start
To stop service: python run_as_service.py stop
To remove service: python run_as_service.py remove
"""
import sys
import os
import time
import threading
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
	import win32serviceutil
	import win32service
	import servicemanager
	HAS_WIN32 = True
except ImportError:
	HAS_WIN32 = False
	print("ERROR: pywin32 not installed. Install with: pip install pywin32")
	sys.exit(1)

class FlaskBackupReceiverService(win32serviceutil.ServiceFramework):
	"""
	Windows Service for Flask backup receiver.
	"""
	_svc_name_ = "FlaskBackupReceiver"
	_svc_display_name_ = "Flask Backup Receiver Service"
	_svc_description_ = "Receives and processes iPhone backup files via Flask API"
	
	def __init__(self, args):
		win32serviceutil.ServiceFramework.__init__(self, args)
		self.stop_event = threading.Event()
		self.app = None
		self.server_thread = None
	
	def SvcStop(self):
		"""
		Stop the service.
		"""
		self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
		self.stop_event.set()
		if self.app:
			# Shutdown Flask app gracefully
			try:
				from flask import request
				func = request.environ.get('werkzeug.server.shutdown')
				if func is None:
					raise RuntimeError('Not running with the Werkzeug Server')
				func()
			except Exception as e:
				print(f"Error shutting down Flask: {e}")
	
	def SvcDoRun(self):
		"""
		Run the service.
		"""
		servicemanager.LogMsg(
			servicemanager.EVENTLOG_INFORMATION_TYPE,
			servicemanager.PYS_SERVICE_STARTED,
			(self._svc_name_, '')
		)
		self.main()
	
	def main(self):
		"""
		Main service loop.
		"""
		try:
			# Import receiver app
			import receiver
			
			# Run Flask app
			receiver.app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)
		except Exception as e:
			servicemanager.LogErrorMsg(f"Service error: {e}")
			raise

if __name__ == '__main__':
	if len(sys.argv) == 1:
		servicemanager.Initialize()
		servicemanager.PrepareToHostSingle(FlaskBackupReceiverService)
		servicemanager.StartServiceCtrlDispatcher()
	else:
		win32serviceutil.HandleCommandLine(FlaskBackupReceiverService)

