# PowerShell script to create a clean shortcut for Dictate on the Desktop
# that runs pythonw.exe directly, registers the AppUserModelID (DictateApp),
# and sets the custom microphone icon for proper Windows notification matching.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrEmpty($ScriptDir)) {
    $ScriptDir = Get-Location
}

$ShortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) "Dictate.lnk"
$Target = Join-Path $ScriptDir ".venv\Scripts\pythonw.exe"
$Arguments = "-m src.main"
$IconPath = Join-Path $ScriptDir "icon.ico"
$AppId = "DictateApp"


Write-Host "Creating/Updating Desktop shortcut at: $ShortcutPath"
Write-Host "Target: $Target"
Write-Host "Arguments: $Arguments"
Write-Host "Working Directory: $ScriptDir"
Write-Host "AppID: $AppId"

try {
    $source = '
    using System;
    using System.Runtime.InteropServices;
    using System.Runtime.InteropServices.ComTypes;

    namespace ShortcutHelper {
        [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
        public class ShellLink {}

        [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
        public interface IShellLinkW {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile, int cchMaxPath, out IntPtr pfd, int fFlags);
            void GetIDList(out IntPtr ppidl);
            void SetIDList(IntPtr pidl);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cchMaxName);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cchMaxPath);
            void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
            void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cchMaxPath);
            void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
            void GetHotkey(out short pwHotkey);
            void SetHotkey(short wHotkey);
            void GetShowCmd(out int piShowCmd);
            void SetShowCmd(int iShowCmd);
            void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cchMaxPath, out int piIcon);
            void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
            void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
            void Resolve(IntPtr hwnd, int fFlags);
            void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
        }

        [StructLayout(LayoutKind.Sequential, Pack = 4)]
        public struct PropertyKey {
            public Guid fmtid;
            public uint pid;
            public PropertyKey(Guid guid, uint id) {
                fmtid = guid;
                pid = id;
            }
        }

        [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
        public interface IPropertyStore {
            void GetCount(out uint cProps);
            void GetAt(uint iProp, out PropertyKey pkey);
            void GetValue(ref PropertyKey pkey, [Out] PropVariant pv);
            void SetValue(ref PropertyKey pkey, PropVariant pv);
            void Commit();
        }

        [StructLayout(LayoutKind.Explicit)]
        public class PropVariant {
            [FieldOffset(0)] public ushort vt;
            [FieldOffset(8)] public IntPtr pointerVal;
            
            public static PropVariant FromString(string value) {
                var pv = new PropVariant();
                pv.vt = 31; // VT_LPWSTR
                pv.pointerVal = Marshal.StringToCoTaskMemUni(value);
                return pv;
            }
        }

        public class Creator {
            public static void Create(string shortcutPath, string targetPath, string workDir, string iconPath, string appId, string arguments) {
                var link = (IShellLinkW)new ShellLink();
                link.SetPath(targetPath);
                link.SetWorkingDirectory(workDir);
                link.SetArguments(arguments);
                link.SetIconLocation(iconPath, 0);
                
                var store = (IPropertyStore)link;
                var appIdKey = new PropertyKey(new Guid("9F4C6855-37D7-4F77-8032-D58D506DB13B"), 5);
                store.SetValue(ref appIdKey, PropVariant.FromString(appId));
                store.Commit();
                
                var file = (IPersistFile)link;
                file.Save(shortcutPath, true);
            }
        }
    }
    '

    Add-Type -TypeDefinition $source
    [ShortcutHelper.Creator]::Create($ShortcutPath, $Target, $ScriptDir, $IconPath, $AppId, $Arguments)

    Write-Host "`nSuccessfully created/updated the Desktop shortcut with AppUserModelID!" -ForegroundColor Green
} catch {
    Write-Warning "`nAn error occurred while creating the shortcut: $_"
}
