# Launch REAPER on a separate Windows desktop so no window ever appears on yours.
param([string]$Desktop = "HermesRig", [string]$Exe = "C:\Program Files\REAPER (x64)\reaper.exe")

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class HiddenDesk {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct STARTUPINFO {
    public int cb;
    public string lpReserved;
    public string lpDesktop;
    public string lpTitle;
    public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars;
    public int dwFillAttribute, dwFlags;
    public short wShowWindow, cbReserved2;
    public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct PROCESS_INFORMATION {
    public IntPtr hProcess, hThread;
    public int dwProcessId, dwThreadId;
  }
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern IntPtr CreateDesktop(string lpszDesktop, string lpszDevice, IntPtr pDevmode, int dwFlags, uint dwDesiredAccess, IntPtr lpsa);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool CreateProcess(string lpApplicationName, string lpCommandLine, IntPtr lpProcessAttributes, IntPtr lpThreadAttributes, bool bInheritHandles, uint dwCreationFlags, IntPtr lpEnvironment, string lpCurrentDirectory, ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);

  public static int Launch(string exe, string desktop) {
    const uint DESKTOP_ALL = 0x000F01FF;
    IntPtr h = CreateDesktop(desktop, null, IntPtr.Zero, 0, DESKTOP_ALL, IntPtr.Zero);
    if (h == IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    STARTUPINFO si = new STARTUPINFO();
    si.cb = Marshal.SizeOf(si);
    si.lpDesktop = desktop;
    si.dwFlags = 1;
    si.wShowWindow = 0;
    PROCESS_INFORMATION pi;
    if (!CreateProcess(exe, null, IntPtr.Zero, IntPtr.Zero, false, 0, IntPtr.Zero, null, ref si, out pi))
      throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
    return pi.dwProcessId;
  }
}
"@

Stop-Process -Name reaper -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$pid2 = [HiddenDesk]::Launch($Exe, $Desktop)
Write-Output "launched pid=$pid2 on desktop '$Desktop'"
Start-Sleep -Seconds 15
$p = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
if ($p) {
  Write-Output ("alive pid={0} title='{1}' hwnd={2} ws={3}" -f $p.Id, $p.MainWindowTitle, $p.MainWindowHandle, $p.WorkingSet64)
} else {
  Write-Output "process died"
}
