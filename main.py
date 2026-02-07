#!/usr/bin/env python3
"""
Printer Auto Setup - Complete Solution
Fixes CUPS issues and restores good UI
"""

import pyudev
import subprocess
import os
import re
import time
import threading
import traceback
import sys
import pickle
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk

# -----------------------------
# Configuration
# -----------------------------
PREDEFINED_DRIVERS = {
    "80Series2": "RongtaPos/Printer80.ppd",
    "80Series": "RongtaPos/Printer80.ppd",
}

# Cache configuration
CACHE_DIR = Path.home() / ".cache" / "printer-autosetup"
CACHE_FILE = CACHE_DIR / "drivers_cache.pkl"
CACHE_MAX_AGE = 86400

# Timeouts
CUPS_CHECK_TIMEOUT = 3
CUPS_OPERATION_TIMEOUT = 10
DRIVER_LOAD_TIMEOUT = 15
SEARCH_DEBOUNCE_MS = 300

# Thread pool
executor = ThreadPoolExecutor(max_workers=2)

# -----------------------------
# Debug Helper
# -----------------------------
def debug_log(message):
    """Log debug messages to terminal"""
    print(f"[DEBUG] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr)
    sys.stderr.flush()

# -----------------------------
# CUPS Health Manager
# -----------------------------
class CUPSHealthManager:
    """Handles CUPS status and recovery"""
    
    @staticmethod
    def get_cups_status():
        """Get CUPS status with safety measures"""
        debug_log("Checking CUPS status...")
        
        status = {
            'cups_active': False,
            'cups_hung': False,
            'stuck_jobs': 0,
            'printers': [],
            'total_printers': 0,
            'problem_printers': 0,
            'error': None
        }
        
        try:
            # Quick check: Is CUPS service running?
            result = subprocess.run(
                ["systemctl", "is-active", "cups"],
                capture_output=True,
                text=True,
                timeout=CUPS_CHECK_TIMEOUT
            )
            status['cups_active'] = (result.returncode == 0)
            
            if not status['cups_active']:
                status['error'] = "CUPS service not active"
                return status
            
            # Check if CUPS is responding (not hung)
            try:
                # Try a simple command that should return quickly
                result = subprocess.run(
                    ["lpstat", "-r"],
                    capture_output=True,
                    text=True,
                    timeout=CUPS_CHECK_TIMEOUT
                )
                if result.returncode != 0:
                    status['cups_hung'] = True
                    status['error'] = "CUPS scheduler not responding"
                    return status
            except subprocess.TimeoutExpired:
                status['cups_hung'] = True
                status['error'] = "CUPS command timed out"
                return status
            
            # Get printer list (if CUPS is healthy)
            try:
                result = subprocess.run(
                    ["lpstat", "-p"],
                    capture_output=True,
                    text=True,
                    timeout=CUPS_CHECK_TIMEOUT
                )
                
                if result.returncode == 0:
                    printers = []
                    for line in result.stdout.split('\n'):
                        if line.startswith('printer'):
                            parts = line.split()
                            if len(parts) >= 2:
                                printer_name = parts[1]
                                state = ' '.join(parts[2:]) if len(parts) > 2 else ""
                                printers.append({
                                    'name': printer_name,
                                    'state': state,
                                    'has_issues': 'processing' in state.lower() or 'stopped' in state.lower()
                                })
                    
                    status['printers'] = printers
                    status['total_printers'] = len(printers)
                    status['problem_printers'] = sum(1 for p in printers if p['has_issues'])
            
            except subprocess.TimeoutExpired:
                # lpstat timed out but CUPS is running
                status['cups_hung'] = True
                status['error'] = "Printer list command timed out"
            
            # Check for stuck jobs
            try:
                result = subprocess.run(
                    ["lpstat", "-o"],
                    capture_output=True,
                    text=True,
                    timeout=CUPS_CHECK_TIMEOUT
                )
                if result.returncode == 0:
                    status['stuck_jobs'] = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
            except:
                pass  # Ignore errors for stuck jobs check
            
        except Exception as e:
            debug_log(f"Error checking CUPS status: {e}")
            status['error'] = str(e)
        
        return status
    
    @staticmethod
    def fix_cups_issues():
        """Fix common CUPS issues"""
        debug_log("Fixing CUPS issues...")
        
        steps = ["=== CUPS Fix Procedure ==="]
        
        try:
            # Step 1: Stop cups-browsed (Ubuntu's auto-discovery that causes conflicts)
            steps.append("1. Stopping cups-browsed...")
            result = subprocess.run(
                ["sudo", "systemctl", "stop", "cups-browsed"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                steps.append("   ✓ cups-browsed stopped")
            else:
                steps.append(f"   ⚠ Could not stop cups-browsed: {result.stderr}")
            
            # Step 2: Cancel all print jobs
            steps.append("2. Cancelling all print jobs...")
            result = subprocess.run(
                ["sudo", "cancel", "-a"],
                capture_output=True,
                text=True,
                timeout=10
            )
            steps.append("   ✓ All jobs cancelled")
            
            # Step 3: Clean spool directory
            steps.append("3. Cleaning spool directory...")
            spool_dir = "/var/spool/cups"
            if os.path.exists(spool_dir):
                try:
                    # Remove only job files, keep directories
                    for item in os.listdir(spool_dir):
                        item_path = os.path.join(spool_dir, item)
                        if os.path.isfile(item_path):
                            os.remove(item_path)
                    steps.append("   ✓ Spool directory cleaned")
                except Exception as e:
                    steps.append(f"   ⚠ Could not clean spool: {e}")
            
            # Step 4: Restart CUPS
            steps.append("4. Restarting CUPS...")
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "cups"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                steps.append("   ✓ CUPS restarted")
                
                # Wait for CUPS to start
                time.sleep(3)
                
                # Verify
                steps.append("5. Verifying CUPS...")
                result = subprocess.run(
                    ["systemctl", "is-active", "cups"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0:
                    steps.append("   ✓ CUPS is active and running")
                    return True, "\n".join(steps)
                else:
                    steps.append("   ✗ CUPS failed to start")
                    return False, "\n".join(steps)
            else:
                steps.append(f"   ✗ Failed to restart CUPS: {result.stderr}")
                return False, "\n".join(steps)
                
        except Exception as e:
            steps.append(f"✗ Error during CUPS fix: {str(e)}")
            debug_log(f"CUPS fix error: {e}")
            return False, "\n".join(steps)
    
    @staticmethod
    def disable_ubuntu_autoconfig():
        """Disable Ubuntu's automatic printer configuration"""
        debug_log("Disabling Ubuntu auto-config...")
        
        try:
            # Stop and disable cups-browsed
            subprocess.run(["sudo", "systemctl", "stop", "cups-browsed"], 
                          capture_output=True, text=True, timeout=5)
            subprocess.run(["sudo", "systemctl", "disable", "cups-browsed"], 
                          capture_output=True, text=True, timeout=5)
            
            # Disable browsing in cups-browsed config
            config = """# Disabled by Printer Auto Setup
Browsing Off
BrowseRemoteProtocols none
CreateIPPPrinterQueues No
"""
            
            with open("/tmp/cups-browsed-disable.conf", "w") as f:
                f.write(config)
            
            subprocess.run(["sudo", "cp", "/tmp/cups-browsed-disable.conf", "/etc/cups/cups-browsed.conf"], 
                          capture_output=True, text=True, timeout=5)
            
            debug_log("Ubuntu auto-config disabled")
            return True
            
        except Exception as e:
            debug_log(f"Failed to disable auto-config: {e}")
            return False
    
    @staticmethod
    def safe_printer_command(command, timeout=CUPS_OPERATION_TIMEOUT):
        """Execute printer command safely with timeout"""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Command timed out after {timeout}s"
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e)
            }

# -----------------------------
# Printer Manager with Working Test Print
# -----------------------------
class PrinterManager:
    """Safe printer operations with working test print"""
    
    @staticmethod
    def get_available_printers():
        """Get printer list safely"""
        debug_log("Getting printer list...")
        
        printers = []
        try:
            result = subprocess.run(
                ["lpstat", "-p"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("printer "):
                        parts = line.split()
                        if len(parts) >= 2:
                            printer_name = parts[1]
                            status = " ".join(parts[2:]) if len(parts) > 2 else ""
                            
                            # Get more details
                            details = PrinterManager.get_printer_details(printer_name)
                            
                            printer_info = {
                                "name": printer_name,
                                "status": status,
                                "description": details.get("description", ""),
                                "location": details.get("location", ""),
                                "uri": details.get("uri", ""),
                                "is_enabled": "enabled" in status.lower(),
                                "has_issues": "processing" in status.lower() or 
                                             "stopped" in status.lower() or
                                             "disabled" in status.lower()
                            }
                            printers.append(printer_info)
            
            debug_log(f"Found {len(printers)} printers")
            return printers
            
        except Exception as e:
            debug_log(f"Error getting printers: {e}")
            return []
    
    @staticmethod
    def get_printer_details(printer_name):
        """Get detailed information about a specific printer"""
        details = {"name": printer_name}
        
        try:
            # Get printer attributes
            result = subprocess.run(
                ["lpstat", "-p", printer_name, "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if "Description:" in line:
                        details["description"] = line.replace("Description:", "").strip()
                    elif "Location:" in line:
                        details["location"] = line.replace("Location:", "").strip()
                    elif "DeviceURI:" in line:
                        details["uri"] = line.replace("DeviceURI:", "").strip()
            
            # Check for active jobs
            result = subprocess.run(
                ["lpstat", "-o", printer_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0 and result.stdout.strip():
                details["active_jobs"] = len(result.stdout.strip().split('\n'))
            else:
                details["active_jobs"] = 0
                
        except Exception as e:
            debug_log(f"Error getting printer details: {e}")
        
        return details
    
    @staticmethod
    def test_printer(printer_name):
        """Test printer - WORKING VERSION"""
        debug_log(f"Testing printer: {printer_name}")
        
        try:
            # First ensure printer is ready - ADD MORE ROBUST CHECKS
            subprocess.run(["sudo", "cupsenable", printer_name], 
                        capture_output=True, text=True, timeout=5)
            subprocess.run(["sudo", "cupsaccept", printer_name], 
                        capture_output=True, text=True, timeout=5)
            
            # Check printer status first
            result = subprocess.run(
                ["lpstat", "-p", printer_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                return False, f"Printer '{printer_name}' not found"
            
            # Wait a bit longer
            time.sleep(3)
            
            # Create simple test content
            test_content = f"""Printer Test Page
    ===================
    Printer: {printer_name}
    Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
    ===================
    This is a test page.
    If you can read this, printing works!
    ===================
    """
            
            # Try multiple methods
            methods = [
                # Method 1: lp with simple options
                (["lp", "-d", printer_name], "Method 1 (lp)"),
                # Method 2: lpr
                (["lpr", "-P", printer_name], "Method 2 (lpr)"),
                # Method 3: lp with raw mode
                (["lp", "-d", printer_name, "-o", "raw"], "Method 3 (raw)")
            ]
            
            for cmd, method_name in methods:
                debug_log(f"Trying {method_name}")
                result = subprocess.run(
                    cmd,
                    input=test_content,
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                
                if result.returncode == 0:
                    job_id = result.stdout.strip() if result.stdout else "unknown"
                    debug_log(f"Test print sent via {method_name}, job: {job_id}")
                    return True, f"✓ Test page sent via {method_name}"
            
            # If all methods fail
            return False, "All print methods failed"
            
        except Exception as e:
            debug_log(f"Exception testing printer: {e}")
            return False, f"Error: {str(e)}"
        
    @staticmethod
    def test_printer_alternative(printer_name):
        """Alternative test print method"""
        try:
            # Create a text file
            test_file = "/tmp/printer_test.txt"
            with open(test_file, "w") as f:
                f.write(f"Test print for {printer_name}\n")
                f.write(f"Time: {time.strftime('%H:%M:%S')}\n")
                f.write("Test successful if printed.\n")
            
            # Use lpr instead of lp
            result = subprocess.run(
                ["lpr", "-P", printer_name, test_file],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # Clean up
            os.remove(test_file)
            
            if result.returncode == 0:
                return True, f"Test sent to '{printer_name}' (alternative method)"
            else:
                return False, f"Print failed: {result.stderr}"
                
        except Exception as e:
            debug_log(f"Alternative test failed: {e}")
            return False, f"All test methods failed: {str(e)}"
    
    @staticmethod
    def delete_printer(printer_name):
        """Delete a printer"""
        try:
            # First cancel all jobs
            subprocess.run(["sudo", "cancel", "-a", printer_name], 
                        capture_output=True, text=True, timeout=5)
            time.sleep(1)
            
            # Delete the printer
            result = subprocess.run(
                ["sudo", "lpadmin", "-x", printer_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                debug_log(f"Deleted printer: {printer_name}")
                return True, f"Printer '{printer_name}' deleted successfully"
            else:
                error_msg = result.stderr if result.stderr else result.stdout
                debug_log(f"Error deleting printer: {error_msg}")
                return False, f"Failed to delete printer: {error_msg}"
                
        except Exception as e:
            debug_log(f"Exception deleting printer: {e}")
            return False, f"Error: {str(e)}"

# -----------------------------
# Driver Cache
# -----------------------------
class DriverCache:
    """Cached driver management"""
    
    def __init__(self):
        self.cache_dir = CACHE_DIR
        self.cache_file = CACHE_FILE
        self.cache_max_age = CACHE_MAX_AGE
    
    def get_drivers_from_system(self):
        """Get drivers from system"""
        try:
            result = subprocess.run(
                ["lpinfo", "-m"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=DRIVER_LOAD_TIMEOUT
            )
            
            if result.returncode == 0:
                drivers = result.stdout.splitlines()
                debug_log(f"Loaded {len(drivers)} drivers from system")
                return drivers
            return []
        except:
            return []
    
    def get_drivers(self, keyword=""):
        """Get drivers with optional keyword filter"""
        drivers = []
        try:
            # Always get fresh list (no cache issues)
            drivers = self.get_drivers_from_system()
            
            if keyword:
                keyword_lower = keyword.lower()
                drivers = [d for d in drivers if keyword_lower in d.lower()]
            
            return drivers[:100]  # Limit to 100 results
            
        except Exception as e:
            debug_log(f"Error getting drivers: {e}")
            return []

# Global instances
cups_manager = CUPSHealthManager()
printer_manager = PrinterManager()
driver_cache = DriverCache()

# -----------------------------
# Helper Functions
# -----------------------------
def safe_name(text):
    return re.sub(r"[^a-zA-Z0-9_]", "_", text)

def extract_model(ieee):
    if not ieee:
        return None
    for part in ieee.split(";"):
        if part.startswith("MODEL:"):
            return part.split(":", 1)[1].strip()
    return None

def get_ieee1284_from_lp():
    """Get printer model info from USB"""
    base = "/sys/class/usbmisc"
    if not os.path.exists(base):
        return None

    for lp in os.listdir(base):
        ieee_path = os.path.join(base, lp, "device", "ieee1284_id")
        if os.path.exists(ieee_path):
            try:
                with open(ieee_path) as f:
                    data = f.read().strip()
                    if data:
                        return data
            except:
                continue
    return None

def change_driver(printer_name, driver_uri):
    """Change printer driver safely"""
    debug_log(f"Changing driver for {printer_name} to {driver_uri}")
    
    try:
        # First cancel any stuck jobs
        subprocess.run(["sudo", "cancel", "-a", printer_name], 
                      capture_output=True, text=True, timeout=5)
        time.sleep(1)
        
        # Check if printer exists
        result = subprocess.run(
            ["lpstat", "-p", printer_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            # Create new printer
            debug_log(f"Creating new printer: {printer_name}")
            result = subprocess.run([
                "sudo", "lpadmin", "-p", printer_name,
                "-v", "usb://", "-E", "-m", driver_uri
            ], capture_output=True, text=True, timeout=15)
        else:
            # Update existing printer
            debug_log(f"Updating existing printer: {printer_name}")
            result = subprocess.run([
                "sudo", "lpadmin", "-p", printer_name,
                "-m", driver_uri, "-E"
            ], capture_output=True, text=True, timeout=15)
        
        if result.returncode != 0:
            error_msg = result.stderr if result.stderr else result.stdout
            debug_log(f"lpadmin failed: {error_msg}")
            return False, f"Failed to configure printer: {error_msg}"
        
        # Enable and accept
        subprocess.run(["sudo", "cupsenable", printer_name], 
                      capture_output=True, text=True, timeout=5)
        subprocess.run(["sudo", "cupsaccept", printer_name], 
                      capture_output=True, text=True, timeout=5)
        time.sleep(2)
        
        debug_log(f"Driver change successful for {printer_name}")
        return True, f"Driver installed successfully for '{printer_name}'"
        
    except Exception as e:
        debug_log(f"Exception changing driver: {e}")
        return False, f"Error: {str(e)}"

# -----------------------------
# Driver Search Dialog
# -----------------------------
class DriverSearchDialog:
    def __init__(self, parent, model=None, printer_name=None):
        self.parent = parent
        self.model = model
        self.printer_name = printer_name
        self.dialog = None
        self.search_task_id = None
        self.current_search = ""
        
    def show(self):
        """Show the search dialog"""
        title = "Manual Driver Search"
        if self.printer_name:
            title = f"Change Driver for {self.printer_name}"
        elif self.model:
            title = f"Select Driver for {self.model}"
        
        self.dialog = Gtk.Dialog(
            title=title,
            parent=self.parent,
            flags=0
        )
        self.dialog.set_modal(True)
        self.dialog.set_default_size(600, 500)
        self.dialog.set_resizable(True)
        
        # Add buttons
        self.dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Select", Gtk.ResponseType.OK
        )
        
        # Get content area
        content_area = self.dialog.get_content_area()
        content_area.set_spacing(10)
        content_area.set_margin_top(10)
        content_area.set_margin_bottom(10)
        content_area.set_margin_start(10)
        content_area.set_margin_end(10)
        
        # Model/Printer info
        if self.model or self.printer_name:
            info_label = Gtk.Label()
            if self.printer_name:
                info_text = f"<b>Printer:</b> {self.printer_name}"
                if self.model:
                    info_text += f"\n<b>Model:</b> {self.model}"
            else:
                info_text = f"<b>Model:</b> {self.model}"
            
            info_label.set_markup(info_text)
            info_label.set_halign(Gtk.Align.START)
            content_area.pack_start(info_label, False, False, 0)
        
        # Search box
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content_area.pack_start(search_box, False, False, 0)
        
        search_label = Gtk.Label(label="Search:")
        search_box.pack_start(search_label, False, False, 0)
        
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Type manufacturer or model name...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_entry.connect("activate", self.on_search_activate)
        search_box.pack_start(self.search_entry, True, True, 0)
        
        # Search status
        self.status_label = Gtk.Label()
        self.status_label.set_halign(Gtk.Align.START)
        content_area.pack_start(self.status_label, False, False, 0)
        
        # Create scrolled window for list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        content_area.pack_start(scrolled, True, True, 0)
        
        # Create list store and tree view
        self.list_store = Gtk.ListStore(str, str)  # Display text, full URI
        self.tree_view = Gtk.TreeView(model=self.list_store)
        self.tree_view.set_headers_visible(False)
        
        # Single column
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("Drivers", renderer, text=0)
        column.set_expand(True)
        self.tree_view.append_column(column)
        
        # Selection
        self.selection = self.tree_view.get_selection()
        
        scrolled.add(self.tree_view)
        
        # Load initial message
        self.update_status("Type to search drivers...")
        self.search_entry.grab_focus()
        
        self.dialog.show_all()
        
        # Run dialog
        response = self.dialog.run()
        
        selected_driver = None
        if response == Gtk.ResponseType.OK:
            selection = self.selection.get_selected()
            if selection[1]:
                selected_driver = self.list_store[selection[1]][1]
        
        self.dialog.destroy()
        
        if self.search_task_id:
            GLib.source_remove(self.search_task_id)
        
        return selected_driver
    
    def update_status(self, message):
        self.status_label.set_text(message)
    
    def on_search_activate(self, entry):
        self.perform_search()
    
    def on_search_changed(self, entry):
        search_text = entry.get_text().strip()
        
        if self.search_task_id:
            GLib.source_remove(self.search_task_id)
        
        self.search_task_id = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS, 
            self.perform_search_debounced, 
            search_text
        )
    
    def perform_search_debounced(self, search_text):
        if search_text != self.current_search:
            self.current_search = search_text
            self.perform_search()
        self.search_task_id = None
        return False
    
    def perform_search(self):
        search_text = self.search_entry.get_text().strip()
        
        if len(search_text) < 2 and search_text != "":
            self.update_status("Please enter at least 2 characters to search")
            self.list_store.clear()
            self.list_store.append(["Type at least 2 characters", ""])
            return
        
        if search_text:
            self.update_status(f"Searching for '{search_text}'...")
        else:
            self.update_status("Showing popular drivers...")
        
        self.list_store.clear()
        self.list_store.append(["Searching... Please wait", ""])
        
        ok_button = self.dialog.get_widget_for_response(Gtk.ResponseType.OK)
        if ok_button:
            ok_button.set_sensitive(False)
        
        executor.submit(self.search_drivers_background, search_text)
    
    def search_drivers_background(self, keyword):
        try:
            drivers = driver_cache.get_drivers(keyword)
            GLib.idle_add(self.update_driver_list, keyword, drivers)
        except Exception as e:
            error_msg = f"Search error: {str(e)}"
            debug_log(error_msg)
            GLib.idle_add(self.update_driver_list, keyword, [error_msg])
    
    def update_driver_list(self, keyword, drivers):
        self.list_store.clear()
        
        ok_button = self.dialog.get_widget_for_response(Gtk.ResponseType.OK)
        
        if not drivers:
            self.list_store.append(["No drivers found", ""])
            if ok_button:
                ok_button.set_sensitive(False)
            self.update_status(f"No drivers found for '{keyword}'")
        elif len(drivers) == 1 and drivers[0].startswith("Error:"):
            self.list_store.append([drivers[0], ""])
            if ok_button:
                ok_button.set_sensitive(False)
            self.update_status(drivers[0])
        else:
            display_drivers = drivers[:100]
            for driver in display_drivers:
                display_text = driver
                if len(display_text) > 80:
                    display_text = display_text[:77] + "..."
                
                uri = driver.split()[0] if driver.split() else driver
                self.list_store.append([display_text, uri])
            
            if len(drivers) > 100:
                remaining = len(drivers) - 100
                self.list_store.append([
                    f"... and {remaining} more drivers. Refine your search.",
                    ""
                ])
            
            if keyword:
                self.update_status(f"Found {len(drivers)} drivers for '{keyword}'")
            else:
                self.update_status(f"Showing {len(display_drivers)} of {len(drivers)} total drivers")
            
            if ok_button and len(drivers) > 0 and not drivers[0].startswith("Error:"):
                ok_button.set_sensitive(True)

# -----------------------------
# Main Application with Good UI
# -----------------------------
class PrinterAutoSetupApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.printer.autosetup.fixed")
        self.monitoring = True
        self.monitor_thread = None
        self.context = None
        
    def do_activate(self):
        # Disable Ubuntu auto-config on startup
        cups_manager.disable_ubuntu_autoconfig()
        
        try:
            self.context = pyudev.Context()
        except Exception as e:
            debug_log(f"Pyudev error: {e}")
        
        self.window = MainWindow(self)
        self.window.present()
        self.add_window(self.window)
        
        # Start monitoring
        self.start_monitoring()

    def start_monitoring(self):
        """Start USB monitoring"""
        if not self.context:
            try:
                self.context = pyudev.Context()
            except:
                return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self.window.monitor_printers,
            daemon=True
        )
        self.monitor_thread.start()

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Printer Auto Setup")
        
        self.set_default_size(800, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_border_width(10)
        
        # Set icon
        try:
            self.set_icon_name("printer")
        except:
            pass
        
        # Main container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(main_box)
        
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        main_box.pack_start(header_box, False, False, 0)
        
        icon = Gtk.Image.new_from_icon_name("printer", Gtk.IconSize.DIALOG)
        header_box.pack_start(icon, False, False, 0)
        
        title = Gtk.Label()
        title.set_markup("<span size='large' weight='bold'>Printer Auto Setup</span>")
        header_box.pack_start(title, True, True, 0)
        
        # CUPS status indicator
        self.cups_status_indicator = Gtk.Label()
        header_box.pack_start(self.cups_status_indicator, False, False, 0)
        
        # Notebook for tabs
        self.notebook = Gtk.Notebook()
        main_box.pack_start(self.notebook, True, True, 0)
        
        # Create tabs
        self.create_monitoring_tab()
        self.create_printers_tab()
        self.create_fix_tab()
        self.create_drivers_tab()
        
        # Footer
        footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        main_box.pack_start(footer_box, False, False, 0)
        
        self.status_label = Gtk.Label()
        self.status_label.set_halign(Gtk.Align.START)
        footer_box.pack_start(self.status_label, True, True, 0)
        
        self.update_time_label = Gtk.Label()
        self.update_time_label.set_halign(Gtk.Align.END)
        footer_box.pack_start(self.update_time_label, False, False, 0)
        
        # Initial update
        self.update_cups_status()
        GLib.timeout_add_seconds(10, self.update_cups_status)
        
        self.show_all()
    
    def create_monitoring_tab(self):
        """Create monitoring tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        
        # Log area
        log_frame = Gtk.Frame(label=" Activity Log ")
        box.pack_start(log_frame, True, True, 0)
        
        self.log_textview = Gtk.TextView()
        self.log_textview.set_editable(False)
        self.log_textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.log_textview.set_monospace(True)
        self.log_buffer = self.log_textview.get_buffer()
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.log_textview)
        log_frame.add(scrolled)
        
        # Initial message
        self.log_message("=" * 60, "gray")
        self.log_message("Printer Auto Setup Application Started", "blue")
        self.log_message("USB printer monitoring is active", "green")
        self.log_message("=" * 60, "gray")
        
        # Buttons
        button_box = Gtk.ButtonBox.new(Gtk.Orientation.HORIZONTAL)
        button_box.set_layout(Gtk.ButtonBoxStyle.CENTER)
        button_box.set_spacing(10)
        box.pack_start(button_box, False, False, 0)
        
        clear_btn = Gtk.Button.new_with_label("Clear Log")
        clear_btn.connect("clicked", self.on_clear_log)
        button_box.add(clear_btn)
        
        test_btn = Gtk.Button.new_with_label("Test USB Detection")
        test_btn.connect("clicked", self.on_test_detection)
        button_box.add(test_btn)
        
        self.notebook.append_page(box, Gtk.Label(label="Monitoring"))
    
    def create_printers_tab(self):
        """Create printers tab with better UI"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        
        # Printer list frame
        list_frame = Gtk.Frame(label=" Available Printers ")
        list_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        box.pack_start(list_frame, True, True, 0)
        
        # Create list store with more columns
        self.printer_list_store = Gtk.ListStore(str, str, str, str, bool)  
        # Name, Status, Description, URI, HasIssues
        
        self.printer_tree = Gtk.TreeView(model=self.printer_list_store)
        self.printer_tree.set_headers_visible(True)
        
        renderer = Gtk.CellRendererText()
        
        # Printer name column
        col1 = Gtk.TreeViewColumn("Printer", renderer, text=0)
        col1.set_expand(True)
        col1.set_min_width(150)
        self.printer_tree.append_column(col1)
        
        # Status column
        col2 = Gtk.TreeViewColumn("Status", renderer, text=1)
        col2.set_min_width(100)
        self.printer_tree.append_column(col2)
        
        # Description column
        col3 = Gtk.TreeViewColumn("Description", renderer, text=2)
        col3.set_expand(True)
        col3.set_min_width(150)
        self.printer_tree.append_column(col3)
        
        # URI column
        col4 = Gtk.TreeViewColumn("URI", renderer, text=3)
        col4.set_min_width(100)
        self.printer_tree.append_column(col4)
        
        self.printer_selection = self.printer_tree.get_selection()
        self.printer_selection.connect("changed", self.on_printer_selection_changed)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.printer_tree)
        list_frame.add(scrolled)
        
        # Printer info label
        self.printer_info_label = Gtk.Label()
        self.printer_info_label.set_line_wrap(True)
        self.printer_info_label.set_halign(Gtk.Align.START)
        box.pack_start(self.printer_info_label, False, False, 0)
        
        # Action buttons
        action_box = Gtk.ButtonBox.new(Gtk.Orientation.HORIZONTAL)
        action_box.set_layout(Gtk.ButtonBoxStyle.CENTER)
        action_box.set_spacing(10)
        box.pack_start(action_box, False, False, 0)
        
        self.refresh_btn = Gtk.Button.new_with_label("Refresh List")
        self.refresh_btn.connect("clicked", self.on_refresh_printers)
        action_box.add(self.refresh_btn)
        
        self.test_btn = Gtk.Button.new_with_label("Test Print")
        self.test_btn.connect("clicked", self.on_test_printer)
        self.test_btn.set_sensitive(False)
        action_box.add(self.test_btn)
        
        self.change_driver_btn = Gtk.Button.new_with_label("Change Driver")
        self.change_driver_btn.connect("clicked", self.on_change_driver)
        self.change_driver_btn.set_sensitive(False)
        action_box.add(self.change_driver_btn)
        
        self.delete_btn = Gtk.Button.new_with_label("Delete Printer")
        self.delete_btn.connect("clicked", self.on_delete_printer)
        self.delete_btn.set_sensitive(False)
        action_box.add(self.delete_btn)
        
        # Initial load
        self.load_printers()
        
        self.notebook.append_page(box, Gtk.Label(label="Printers"))
    
    def create_fix_tab(self):
        """Create CUPS fix tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        
        # Status info
        status_frame = Gtk.Frame(label=" CUPS Status Information ")
        status_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        box.pack_start(status_frame, False, False, 0)
        
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        status_box.set_margin_top(10)
        status_box.set_margin_bottom(10)
        status_box.set_margin_start(10)
        status_box.set_margin_end(10)
        status_frame.add(status_box)
        
        self.cups_status_text = Gtk.Label()
        self.cups_status_text.set_line_wrap(True)
        self.cups_status_text.set_halign(Gtk.Align.START)
        status_box.pack_start(self.cups_status_text, False, False, 0)
        
        # Fix buttons frame
        fix_frame = Gtk.Frame(label=" Fix CUPS Problems ")
        fix_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        box.pack_start(fix_frame, True, True, 0)
        
        fix_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        fix_box.set_margin_top(10)
        fix_box.set_margin_bottom(10)
        fix_box.set_margin_start(10)
        fix_box.set_margin_end(10)
        fix_frame.add(fix_box)
        
        # Issue explanation
        issues = Gtk.Label()
        issues.set_markup(
            "<b>Common CUPS Problems:</b>\n\n"
            "• CUPS service hanging or crashing\n"
            "• Printer stuck in 'Processing' status\n"
            "• Ubuntu auto-configuration conflicts\n"
            "• Print jobs not completing\n"
            "• CUPS commands timing out"
        )
        issues.set_line_wrap(True)
        issues.set_halign(Gtk.Align.START)
        fix_box.pack_start(issues, False, False, 0)
        
        # Fix buttons in a grid
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        fix_box.pack_start(grid, False, False, 0)
        
        self.fix_cups_btn = Gtk.Button.new_with_label("Fix CUPS Issues")
        self.fix_cups_btn.connect("clicked", self.on_fix_cups)
        self.fix_cups_btn.set_hexpand(True)
        grid.attach(self.fix_cups_btn, 0, 0, 1, 1)
        
        self.disable_auto_btn = Gtk.Button.new_with_label("Disable Auto-Config")
        self.disable_auto_btn.connect("clicked", self.on_disable_autoconfig)
        self.disable_auto_btn.set_hexpand(True)
        grid.attach(self.disable_auto_btn, 1, 0, 1, 1)
        
        self.restart_cups_btn = Gtk.Button.new_with_label("Restart CUPS")
        self.restart_cups_btn.connect("clicked", self.on_restart_cups)
        self.restart_cups_btn.set_hexpand(True)
        grid.attach(self.restart_cups_btn, 0, 1, 1, 1)
        
        self.clear_jobs_btn = Gtk.Button.new_with_label("Clear Stuck Jobs")
        self.clear_jobs_btn.connect("clicked", self.on_clear_stuck_jobs)
        self.clear_jobs_btn.set_hexpand(True)
        grid.attach(self.clear_jobs_btn, 1, 1, 1, 1)
        
        # Results area
        results_frame = Gtk.Frame(label=" Fix Results ")
        results_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        box.pack_start(results_frame, True, True, 0)
        
        self.results_textview = Gtk.TextView()
        self.results_textview.set_editable(False)
        self.results_textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.results_textview.set_monospace(True)
        self.results_buffer = self.results_textview.get_buffer()
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.results_textview)
        results_frame.add(scrolled)
        
        self.notebook.append_page(box, Gtk.Label(label="Fix CUPS"))
    
    def create_drivers_tab(self):
        """Create drivers tab"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        
        # Predefined drivers
        drivers_frame = Gtk.Frame(label=" Predefined Driver Mappings ")
        drivers_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        box.pack_start(drivers_frame, True, True, 0)
        
        drivers_list = Gtk.ListStore(str, str)
        for model, driver in PREDEFINED_DRIVERS.items():
            drivers_list.append([model, driver])
        
        treeview = Gtk.TreeView(model=drivers_list)
        treeview.set_headers_visible(True)
        
        renderer = Gtk.CellRendererText()
        col1 = Gtk.TreeViewColumn("Printer Model", renderer, text=0)
        col1.set_expand(True)
        col1.set_min_width(200)
        treeview.append_column(col1)
        
        col2 = Gtk.TreeViewColumn("Driver PPD", renderer, text=1)
        col2.set_expand(True)
        treeview.append_column(col2)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(treeview)
        drivers_frame.add(scrolled)
        
        # Driver management buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.CENTER)
        box.pack_start(button_box, False, False, 0)
        
        self.search_drivers_btn = Gtk.Button.new_with_label("Search for Drivers")
        self.search_drivers_btn.connect("clicked", self.on_search_drivers)
        button_box.add(self.search_drivers_btn)
        
        self.install_manual_btn = Gtk.Button.new_with_label("Install New Printer")
        self.install_manual_btn.connect("clicked", self.on_install_manual)
        button_box.add(self.install_manual_btn)
        
        self.notebook.append_page(box, Gtk.Label(label="Drivers"))
    
    def log_message(self, message, color=None):
        """Log message with timestamp"""
        end_iter = self.log_buffer.get_end_iter()
        timestamp = time.strftime("%H:%M:%S")
        
        if color:
            tag = self.log_buffer.create_tag(color, foreground=color)
            self.log_buffer.insert_with_tags(end_iter, f"[{timestamp}] {message}\n", tag)
        else:
            self.log_buffer.insert(end_iter, f"[{timestamp}] {message}\n")
        
        self.log_textview.scroll_to_iter(end_iter, 0, False, 0, 0)
        debug_log(f"UI Log: {message}")
    
    def log_results(self, message):
        """Log to results area"""
        end_iter = self.results_buffer.get_end_iter()
        timestamp = time.strftime("%H:%M:%S")
        self.results_buffer.insert(end_iter, f"[{timestamp}] {message}\n")
        self.results_textview.scroll_to_iter(end_iter, 0, False, 0, 0)
    
    def update_cups_status(self):
        """Update CUPS status display"""
        def update_in_background():
            status = cups_manager.get_cups_status()
            GLib.idle_add(self.update_cups_display, status)
        
        threading.Thread(target=update_in_background, daemon=True).start()
        return True
    
    def update_cups_display(self, status):
        """Update CUPS status in UI"""
        if status['cups_hung'] or not status['cups_active']:
            # CUPS has issues
            self.cups_status_indicator.set_markup("<span color='red' weight='bold'>⚠ CUPS PROBLEMS</span>")
            
            status_text = "<span color='red'><b>⚠ CUPS STATUS: PROBLEMS DETECTED</b></span>\n\n"
            if status['error']:
                status_text += f"<b>Error:</b> {status['error']}\n"
            status_text += f"<b>Service Active:</b> {status['cups_active']}\n"
            status_text += f"<b>Printers Found:</b> {status['total_printers']}\n"
            status_text += f"<b>Stuck Jobs:</b> {status['stuck_jobs']}\n\n"
            status_text += "<i>Use the buttons below to fix CUPS issues</i>"
            
            self.cups_status_text.set_markup(status_text)
            self.status_label.set_markup("<span color='red'>CUPS needs attention - Use Fix tab</span>")
            
        else:
            # CUPS is healthy
            self.cups_status_indicator.set_markup("<span color='green' weight='bold'>✓ CUPS OK</span>")
            
            status_text = "<span color='green'><b>✓ CUPS STATUS: ACTIVE AND HEALTHY</b></span>\n\n"
            status_text += f"<b>Printers:</b> {status['total_printers']}\n"
            status_text += f"<b>With Issues:</b> {status['problem_printers']}\n"
            status_text += f"<b>Stuck Jobs:</b> {status['stuck_jobs']}\n\n"
            status_text += "<i>CUPS is running normally</i>"
            
            self.cups_status_text.set_markup(status_text)
            self.status_label.set_text(f"CUPS active - {status['total_printers']} printers")
        
        self.update_time_label.set_text(f"Updated: {time.strftime('%H:%M:%S')}")
    
    def load_printers(self):
        """Load printer list"""
        self.printer_list_store.clear()
        self.printer_list_store.append(["Loading printers...", "", "", "", False])
        
        def load_in_background():
            printers = printer_manager.get_available_printers()
            GLib.idle_add(self.update_printer_list, printers)
        
        threading.Thread(target=load_in_background, daemon=True).start()
    
    def update_printer_list(self, printers):
        """Update printer list display"""
        self.printer_list_store.clear()
        
        if not printers:
            self.printer_list_store.append(["No printers found", "", "", "", False])
            self.printer_info_label.set_text("No printers available")
        else:
            for printer in printers:
                status = printer.get("status", "")[:30]
                description = printer.get("description", "")[:40]
                uri = printer.get("uri", "")[:30]
                has_issues = printer.get("has_issues", False)
                
                self.printer_list_store.append([printer["name"], status, description, uri, has_issues])
            
            self.printer_info_label.set_text(f"Found {len(printers)} printers")
    
    def on_printer_selection_changed(self, selection):
        """Handle printer selection change"""
        model, tree_iter = selection.get_selected()
        if tree_iter:
            printer_name = model[tree_iter][0]
            has_issues = model[tree_iter][4]
            
            # Enable buttons
            self.test_btn.set_sensitive(True)
            self.change_driver_btn.set_sensitive(True)
            self.delete_btn.set_sensitive(True)
            
            # Show details
            details = printer_manager.get_printer_details(printer_name)
            info_text = f"<b>Printer:</b> {printer_name}\n"
            info_text += f"<b>Status:</b> {details.get('state', 'Unknown')}\n"
            info_text += f"<b>Description:</b> {details.get('description', 'None')}\n"
            info_text += f"<b>Location:</b> {details.get('location', 'None')}\n"
            info_text += f"<b>URI:</b> {details.get('uri', 'Unknown')}"
            
            self.printer_info_label.set_markup(info_text)
        else:
            # No selection
            self.test_btn.set_sensitive(False)
            self.change_driver_btn.set_sensitive(False)
            self.delete_btn.set_sensitive(False)
            self.printer_info_label.set_text("Select a printer to see details")
    
    # Event handlers
    def on_clear_log(self, button):
        self.log_buffer.set_text("")
        self.log_message("Log cleared", "gray")
    
    def on_test_detection(self, button):
        self.log_message("Testing USB printer detection...", "blue")
        
        def test_in_background():
            ieee = get_ieee1284_from_lp()
            if ieee:
                model = extract_model(ieee)
                if model:
                    GLib.idle_add(lambda: self.log_message(f"✓ Found printer: {model}", "green"))
                else:
                    GLib.idle_add(lambda: self.log_message("⚠ Found printer but could not read model", "orange"))
            else:
                GLib.idle_add(lambda: self.log_message("✗ No printer detected", "red"))
        
        threading.Thread(target=test_in_background, daemon=True).start()
    
    def on_refresh_printers(self, button):
        self.load_printers()
        self.log_message("Refreshed printer list", "blue")
    
    def on_test_printer(self, button):
        selection = self.printer_selection.get_selected()
        if selection[1]:
            printer_name = self.printer_list_store[selection[1]][0]
            self.log_message(f"Testing printer: {printer_name}", "blue")
            
            # Show progress
            progress_dialog = Gtk.Dialog(title="Test Print", parent=self, flags=0)
            progress_dialog.set_modal(True)
            progress_dialog.set_default_size(300, 100)
            
            content = progress_dialog.get_content_area()
            content.set_margin_top(10)
            content.set_margin_bottom(10)
            content.set_margin_start(10)
            content.set_margin_end(10)
            
            label = Gtk.Label(label=f"Sending test page to:\n{printer_name}")
            content.pack_start(label, True, True, 0)
            
            progress_bar = Gtk.ProgressBar()
            progress_bar.set_pulse_step(0.1)
            content.pack_start(progress_bar, True, True, 0)
            
            progress_dialog.show_all()
            
            def test_in_background():
                success, message = printer_manager.test_printer(printer_name)
                GLib.idle_add(progress_dialog.destroy)
                if success:
                    GLib.idle_add(lambda: self.log_message(f"✓ {message}", "green"))
                else:
                    GLib.idle_add(lambda: self.log_message(f"✗ {message}", "red"))
            
            threading.Thread(target=test_in_background, daemon=True).start()
            
            # Pulse progress bar
            def pulse_progress():
                if progress_bar.is_visible():
                    progress_bar.pulse()
                    return True
                return False
            
            GLib.timeout_add(200, pulse_progress)
            
            # Run dialog
            def run_dialog():
                progress_dialog.run()
                progress_dialog.destroy()
            
            threading.Thread(target=run_dialog, daemon=True).start()
    
    def on_change_driver(self, button):
        selection = self.printer_selection.get_selected()
        if selection[1]:
            printer_name = self.printer_list_store[selection[1]][0]
            
            # Get printer details
            details = printer_manager.get_printer_details(printer_name)
            model = None
            if details and "description" in details:
                model = details["description"].split()[0] if details["description"] else None
            
            search_dialog = DriverSearchDialog(self, model=model, printer_name=printer_name)
            selected_driver = search_dialog.show()
            
            if selected_driver:
                self.install_driver(printer_name, selected_driver, is_existing=True)
    
    def on_delete_printer(self, button):
        selection = self.printer_selection.get_selected()
        if selection[1]:
            printer_name = self.printer_list_store[selection[1]][0]
            
            # Confirm deletion
            dialog = Gtk.MessageDialog(
                parent=self,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.YES_NO,
                text=f"Delete printer '{printer_name}'?"
            )
            dialog.format_secondary_text("This action cannot be undone.")
            
            response = dialog.run()
            dialog.destroy()
            
            if response == Gtk.ResponseType.YES:
                self.log_message(f"Deleting printer: {printer_name}", "orange")
                
                def delete_in_background():
                    success, message = printer_manager.delete_printer(printer_name)
                    if success:
                        GLib.idle_add(lambda: self.log_message(f"✓ {message}", "green"))
                        GLib.idle_add(self.load_printers)
                    else:
                        GLib.idle_add(lambda: self.log_message(f"✗ {message}", "red"))
                
                threading.Thread(target=delete_in_background, daemon=True).start()
    
    def on_fix_cups(self, button):
        """Fix CUPS issues"""
        self.log_message("Running CUPS fix procedure...", "blue")
        self.log_results("=== Starting CUPS Fix ===")
        
        def fix_in_background():
            success, message = cups_manager.fix_cups_issues()
            if success:
                GLib.idle_add(lambda: self.log_message("✓ CUPS fixed successfully", "green"))
                GLib.idle_add(lambda: self.log_results("✓ " + message))
            else:
                GLib.idle_add(lambda: self.log_message("✗ CUPS fix failed", "red"))
                GLib.idle_add(lambda: self.log_results("✗ " + message))
            
            # Refresh status
            GLib.idle_add(self.update_cups_status)
            GLib.idle_add(self.load_printers)
        
        threading.Thread(target=fix_in_background, daemon=True).start()
    
    def on_disable_autoconfig(self, button):
        """Disable Ubuntu auto-config"""
        self.log_message("Disabling Ubuntu auto-configuration...", "blue")
        self.log_results("Disabling Ubuntu auto-configuration...")
        
        def disable_in_background():
            success = cups_manager.disable_ubuntu_autoconfig()
            if success:
                GLib.idle_add(lambda: self.log_message("✓ Auto-config disabled", "green"))
                GLib.idle_add(lambda: self.log_results("✓ Ubuntu auto-configuration disabled"))
            else:
                GLib.idle_add(lambda: self.log_message("✗ Failed to disable auto-config", "red"))
                GLib.idle_add(lambda: self.log_results("✗ Failed to disable auto-config"))
        
        threading.Thread(target=disable_in_background, daemon=True).start()
    
    def on_restart_cups(self, button):
        """Restart CUPS service"""
        self.log_message("Restarting CUPS service...", "blue")
        self.log_results("Restarting CUPS service...")
        
        def restart_in_background():
            result = subprocess.run(
                ["sudo", "systemctl", "restart", "cups"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                GLib.idle_add(lambda: self.log_message("✓ CUPS restarted", "green"))
                GLib.idle_add(lambda: self.log_results("✓ CUPS service restarted"))
                time.sleep(2)
                GLib.idle_add(self.update_cups_status)
            else:
                error_msg = result.stderr if result.stderr else result.stdout
                GLib.idle_add(lambda: self.log_message(f"✗ Failed: {error_msg}", "red"))
                GLib.idle_add(lambda: self.log_results(f"✗ Failed: {error_msg}"))
        
        threading.Thread(target=restart_in_background, daemon=True).start()
    
    def on_clear_stuck_jobs(self, button):
        """Clear stuck print jobs"""
        self.log_message("Clearing all stuck print jobs...", "blue")
        self.log_results("Clearing stuck print jobs...")
        
        def clear_in_background():
            result = subprocess.run(
                ["sudo", "cancel", "-a"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                GLib.idle_add(lambda: self.log_message("✓ All jobs cancelled", "green"))
                GLib.idle_add(lambda: self.log_results("✓ All print jobs cancelled"))
                GLib.idle_add(self.update_cups_status)
            else:
                error_msg = result.stderr if result.stderr else result.stdout
                GLib.idle_add(lambda: self.log_message(f"✗ Failed: {error_msg}", "red"))
                GLib.idle_add(lambda: self.log_results(f"✗ Failed: {error_msg}"))
        
        threading.Thread(target=clear_in_background, daemon=True).start()
    
    def on_search_drivers(self, button):
        """Search for drivers"""
        search_dialog = DriverSearchDialog(self)
        selected_driver = search_dialog.show()
        
        if selected_driver:
            self.log_message(f"Selected driver: {selected_driver}", "blue")
            # Ask for printer name
            dialog = Gtk.Dialog(title="Enter Printer Name", parent=self, flags=0)
            dialog.set_modal(True)
            dialog.set_default_size(300, 150)
            
            dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Install", Gtk.ResponseType.OK)
            
            content = dialog.get_content_area()
            content.set_margin_top(10)
            content.set_margin_bottom(10)
            content.set_margin_start(10)
            content.set_margin_end(10)
            
            label = Gtk.Label(label="Enter name for printer:")
            content.pack_start(label, False, False, 0)
            
            name_entry = Gtk.Entry()
            name_entry.set_placeholder_text("e.g., Office_Printer")
            content.pack_start(name_entry, True, True, 0)
            
            dialog.show_all()
            response = dialog.run()
            printer_name = name_entry.get_text().strip()
            dialog.destroy()
            
            if response == Gtk.ResponseType.OK and printer_name:
                self.install_driver(printer_name, selected_driver)
    
    def on_install_manual(self, button):
        """Manual printer installation"""
        dialog = Gtk.Dialog(title="Install New Printer", parent=self, flags=0)
        dialog.set_modal(True)
        dialog.set_default_size(400, 200)
        
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Install", Gtk.ResponseType.OK)
        
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)
        
        # Printer name
        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(name_box, False, False, 0)
        
        name_label = Gtk.Label(label="Printer Name:")
        name_box.pack_start(name_label, False, False, 0)
        
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("e.g., Office_Printer")
        name_box.pack_start(name_entry, True, True, 0)
        
        # Driver URI
        driver_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        content.pack_start(driver_box, False, False, 0)
        
        driver_label = Gtk.Label(label="Driver PPD:")
        driver_box.pack_start(driver_label, False, False, 0)
        
        driver_entry = Gtk.Entry()
        driver_entry.set_placeholder_text("e.g., drv:///sample.drv/generic.ppd")
        driver_entry.set_text("drv:///sample.drv/generic.ppd")
        driver_box.pack_start(driver_entry, True, True, 0)
        
        dialog.show_all()
        response = dialog.run()
        
        if response == Gtk.ResponseType.OK:
            printer_name = name_entry.get_text().strip()
            driver_uri = driver_entry.get_text().strip()
            
            if printer_name and driver_uri:
                dialog.destroy()
                self.install_driver(printer_name, driver_uri)
            else:
                self.log_message("Error: Printer name and driver are required", "red")
                dialog.destroy()
        else:
            dialog.destroy()
    
    def install_driver(self, printer_name, driver_uri, is_existing=False):
        """Install a printer"""
        action = "Updating" if is_existing else "Installing"
        self.log_message(f"{action} driver for '{printer_name}'...", "blue")
        
        def install_in_background():
            success, message = change_driver(printer_name, driver_uri)
            if success:
                GLib.idle_add(lambda: self.log_message(f"✓ {message}", "green"))
                if not is_existing:
                    GLib.idle_add(self.load_printers)
            else:
                GLib.idle_add(lambda: self.log_message(f"✗ {message}", "red"))
        
        threading.Thread(target=install_in_background, daemon=True).start()
    
    def monitor_printers(self):
        """Monitor for USB printers"""
        while self.get_application().monitoring:
            try:
                monitor = pyudev.Monitor.from_netlink(self.get_application().context)
                monitor.filter_by(subsystem="usb")
                debug_log("USB monitor initialized")
                
                for device in iter(monitor.poll, None):
                    if not self.get_application().monitoring:
                        break
                    
                    if device.action == "add" and device.get("DEVTYPE") == "usb_device":
                        debug_log(f"USB device added: {device}")
                        GLib.idle_add(self.process_usb_device)
                    
            except Exception as e:
                debug_log(f"Monitor error: {e}")
                time.sleep(5)
    
    def process_usb_device(self):
        """Process USB device detection"""
        time.sleep(1)  # Wait for device initialization
        
        ieee = get_ieee1284_from_lp()
        if not ieee:
            return
        
        model = extract_model(ieee)
        if not model:
            return
        
        self.log_message(f"🔌 USB printer detected: {model}", "blue")
        
        # Ask to install
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Printer Detected: {model}"
        )
        dialog.format_secondary_text("Would you like to install this printer?")
        
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.YES:
            printer_name = safe_name(model)
            
            # Try predefined driver first
            if model in PREDEFINED_DRIVERS:
                self.install_driver(printer_name, PREDEFINED_DRIVERS[model])
            else:
                # Ask for driver selection
                search_dialog = DriverSearchDialog(self, model=model)
                selected_driver = search_dialog.show()
                
                if selected_driver:
                    self.install_driver(printer_name, selected_driver)
                else:
                    # Use generic driver
                    self.install_driver(printer_name, "drv:///sample.drv/generic.ppd")

# -----------------------------
# Main
# -----------------------------
def main():
    print("Printer Auto Setup - Complete Solution")
    print("======================================")
    print("Features:")
    print("  • CUPS health monitoring")
    print("  • Working test print functionality")
    print("  • Ubuntu auto-config disable")
    print("  • USB printer auto-detection")
    print("  • Driver search and management")
    print("  • Printer list with details")
    print("  • CUPS issue fixing tools")
    print("")
    
    # Check for required packages
    try:
        import pyudev
    except ImportError:
        print("Missing required package: pyudev")
        print("Install it with: pip install pyudev")
        return 1
    
    # Check for CUPS
    try:
        result = subprocess.run(["which", "lpstat"], capture_output=True, text=True)
        if result.returncode != 0:
            print("CUPS is not installed or not in PATH")
            print("Install it with: sudo apt-get install cups cups-client")
            return 1
    except:
        print("Could not check for CUPS installation")
    
    try:
        app = PrinterAutoSetupApp()
        return app.run(sys.argv)
    except Exception as e:
        print(f"\nFatal error: {e}")
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())