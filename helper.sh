#!/bin/bash
# save as: stop-ubuntu-printers.sh
# run with: sudo ./stop-ubuntu-printers.sh

echo "=== COMPLETELY STOPPING UBUNTU PRINTER SERVICES ==="

# 1. STOP and DISABLE all printer-related services
echo "1. Stopping and disabling services..."
sudo systemctl stop cups-browsed
sudo systemctl disable cups-browsed
sudo systemctl mask cups-browsed  # PREVENTS auto-start

sudo systemctl stop cups

# 2. KILL all running printer processes
echo "2. Killing printer processes..."
sudo pkill -9 cups-browsed
sudo pkill -9 cupsd
sudo pkill -9 system-config-printer
sudo pkill -9 printer

# 3. REMOVE auto-start configurations
echo "3. Removing auto-start configs..."
sudo rm -f /etc/xdg/autostart/print-applet.desktop
sudo rm -f /etc/xdg/autostart/cups*.desktop

# 4. DISABLE udev rules that trigger printer detection
echo "4. Disabling udev rules..."
sudo mv /lib/udev/rules.d/70-printers.rules /lib/udev/rules.d/70-printers.rules.DISABLED 2>/dev/null || true
sudo mv /etc/udev/rules.d/70-printers.rules /etc/udev/rules.d/70-printers.rules.DISABLED 2>/dev/null || true

# 5. BLOCK printer-related DBus services
echo "5. Blocking DBus services..."
cat << EOF | sudo tee /etc/dbus-1/system.d/org.freedesktop.Avahi-cups-browsed.conf > /dev/null
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy context="default">
    <deny own="org.freedesktop.Avahi.cups-browsing"/>
  </policy>
</busconfig>
EOF

# 6. DISABLE GNOME/Unity printer integration
echo "6. Disabling GNOME printer integration..."
gsettings set org.gnome.desktop.printer remember-recent-printers false 2>/dev/null || true
gsettings set org.gnome.settings-daemon.plugins.print-notifications active false 2>/dev/null || true

# 7. CREATE blocking config files
echo "7. Creating blocking configs..."

# Block CUPS browsing
cat << EOF | sudo tee /etc/cups/cups-browsed.conf
# COMPLETELY DISABLED by Printer Auto Setup
Browsing Off
BrowseRemoteProtocols none
BrowseLocalProtocols none
CreateIPPPrinterQueues No
BrowseAllow none
EOF

# Block CUPS auto-discovery
cat << EOF | sudo tee /etc/cups/cupsd.conf.BLOCK
# Added to block auto-discovery
BrowseDNSSDSubTypes _cups,_print
BrowseOrder deny,allow
BrowseDeny All
EOF

sudo cp /etc/cups/cupsd.conf /etc/cups/cupsd.conf.BACKUP
sudo cat /etc/cups/cupsd.conf.BLOCK >> /etc/cups/cupsd.conf

# 8. REMOVE printer packages (optional - comment out if you want to keep)
# echo "8. Removing printer packages..."
# sudo apt-get remove --purge -y cups-browsed printer-driver-* system-config-prinder

# 9. PREVENT printer service installation
echo "9. Preventing future installations..."
cat << EOF | sudo tee /etc/apt/preferences.d/block-printers
Package: cups-browsed
Pin: release *
Pin-Priority: -1

Package: system-config-prinder
Pin: release *
Pin-Priority: -1

Package: printer-driver-*
Pin: release *
Pin-Priority: -1
EOF

# 10. CREATE a systemd service to KILL any printer processes that start
echo "10. Creating printer killer service..."
cat << EOF | sudo tee /etc/systemd/system/kill-printers.service
[Unit]
Description=Kill Ubuntu Printer Services
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c "pkill -9 cups-browsed; pkill -9 system-config-prinder; exit 0"
ExecStop=/bin/true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable kill-printers.service
sudo systemctl start kill-printers.service
sudo systemctl start cups

echo "=== DONE ==="
echo "Ubuntu printer services are COMPLETELY disabled."
echo "Reboot to ensure all changes take effect."