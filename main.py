import cups
import subprocess

conn = cups.Connection()

# ---------------------------
# 1. Detect printers known to CUPS
# ---------------------------
def list_printers():
    printers = conn.getPrinters()
    if not printers:
        print("No printers found in CUPS.")
        return {}

    print("\nDetected printers:\n")
    for i, (name, attrs) in enumerate(printers.items(), 1):
        print(f"{i}. {name}")
        print(f"   Model : {attrs.get('printer-make-and-model')}")
        print(f"   State : {attrs.get('printer-state-message')}")
        print(f"   URI   : {attrs.get('device-uri')}\n")
    return printers


# ---------------------------
# 2. Show current driver (PPD)
# ---------------------------
def show_current_driver(printer_name):
    try:
        ppd = conn.getPPD(printer_name)
        print(f"\nCurrent driver (PPD): {ppd}")
    except cups.IPPError:
        print("\nCurrent driver: Driverless (IPP Everywhere)")


# ---------------------------
# 3. Get all available drivers
# ---------------------------
def get_all_drivers():
    result = subprocess.run(
        ["lpinfo", "-m"],
        stdout=subprocess.PIPE,
        text=True
    )
    return result.stdout.splitlines()


# ---------------------------
# 4. Search & display drivers
# ---------------------------
def search_drivers(drivers):
    keyword = input(
        "\nSearch driver (manufacturer/model) "
        "[press Enter to show all]: "
    ).lower()

    if keyword:
        filtered = [d for d in drivers if keyword in d.lower()]
    else:
        filtered = drivers

    if not filtered:
        print("\n❌ No drivers matched your search.")
        return []

    print("\nAvailable drivers:\n")
    for i, drv in enumerate(filtered, 1):
        print(f"{i}. {drv}")

    return filtered


# ---------------------------
# 5. Change printer driver
# ---------------------------
def change_driver(printer_name, driver_uri):
    try:
        subprocess.run(
            [
                "sudo",
                "lpadmin",
                "-p", printer_name,
                "-m", driver_uri
            ],
            check=True
        )

        subprocess.run(["sudo", "cupsenable", printer_name], check=True)
        subprocess.run(["sudo", "cupsaccept", printer_name], check=True)

        print("\n✅ Driver changed successfully.")

    except subprocess.CalledProcessError as e:
        print("\n❌ Failed to change driver.")
        print(e)

# ---------------------------
# 6. Main logic
# ---------------------------
def main():
    printers = list_printers()
    if not printers:
        return

    printer_names = list(printers.keys())

    choice = int(input("Select printer number: ")) - 1
    printer_name = printer_names[choice]

    show_current_driver(printer_name)

    all_drivers = get_all_drivers()

    while True:
        drivers = search_drivers(all_drivers)
        if not drivers:
            retry = input("\nSearch again? (y/n): ").lower()
            if retry != "y":
                return
            continue

        change = input("\nDo you want to change driver? (y/n): ").lower()
        if change != "y":
            return

        drv_choice = int(input("Select driver number: ")) - 1
        driver_uri = drivers[drv_choice].split()[0]
        print(driver_uri)
        change_driver(printer_name, driver_uri)
        return


if __name__ == "__main__":
    main()
