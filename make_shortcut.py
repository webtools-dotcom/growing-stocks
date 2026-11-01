"""Put a 'Rising Stocks' shortcut on the desktop (Windows) that opens the app with a double-click.
Usage: python make_shortcut.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if sys.platform != "win32":
    sys.exit(f"on macOS/Linux run:  {Path(sys.executable).name} {HERE / 'launch.pyw'}")

pythonw = Path(sys.executable).with_name("pythonw.exe")
ps = f"""
$desk = [Environment]::GetFolderPath('Desktop')
$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk 'Rising Stocks.lnk'))
$s.TargetPath = '{pythonw}'
$s.Arguments = '"{HERE / 'launch.pyw'}"'
$s.WorkingDirectory = '{HERE}'
$s.Description = 'Rising Stocks Terminal'
$s.IconLocation = '{Path(r"C:\Windows\System32\imageres.dll")},-1024'
$s.Save()
Write-Output (Join-Path $desk 'Rising Stocks.lnk')
"""
out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
print(out.stdout.strip() or out.stderr.strip())
