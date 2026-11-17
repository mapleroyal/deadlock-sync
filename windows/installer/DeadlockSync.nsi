; Build using tools/build-setup.py. Payload is verified and staged by that builder.
Unicode true
RequestExecutionLevel user
ManifestSupportedOS Win10
ManifestDPIAware true
SetCompressor /SOLID lzma
SetCompressorDictSize 32
CRCCheck force
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinVer.nsh"
!include "x64.nsh"
!include "nsDialogs.nsh"

!ifndef VERSION
  !error "VERSION is required"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD is required"
!endif
!ifndef OUTPUT
  !error "OUTPUT is required"
!endif

!define PRODUCT "Deadlock Sync"
!define PRODUCT_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\DeadlockSync"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
Name "${PRODUCT}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\DeadlockSync"
BrandingText "Deadlock Sync"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "${PRODUCT}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "FileDescription" "Deadlock Sync Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright 2026 mapleroyal"
ShowInstDetails nevershow
ShowUninstDetails nevershow

Var SetupMutex
Var AutoStart
Var NoStart
Var Options
Var CheckStart
Var Dialog
Var Result
Var HadApp

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Welcome to Deadlock Sync"
!define MUI_WELCOMEPAGE_TEXT "Keep your Deadlock matches syncing in the background.$\r$\n$\r$\nSetup installs the app for your Windows account. Everything it needs is included.$\r$\n$\r$\nYour Steam sign-in stays in Steam. History recovery uses the Steam accounts remembered on this PC.$\r$\n$\r$\nClick Next to continue."
!insertmacro MUI_PAGE_WELCOME
Page custom OptionsCreate OptionsLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "Deadlock Sync is installed"
!define MUI_FINISHPAGE_TEXT "Click Finish to open Deadlock Sync.$\r$\n$\r$\nLook for the gamepad near the Windows clock while syncing is enabled. Click it for account status and progress."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start Deadlock Sync"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchSync
!define MUI_PAGE_CUSTOMFUNCTION_SHOW FinishShown
!insertmacro MUI_PAGE_FINISH

!define MUI_UNCONFIRMPAGE_TEXT_TOP "Remove Deadlock Sync from this Windows account?"
!define MUI_UNCONFIRMPAGE_TEXT_LOCATION "Your saved cooldowns and match recovery records will be kept. Steam and Deadlock will not be changed."
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "Deadlock Sync was removed"
!define MUI_FINISHPAGE_TEXT "The app and its Windows startup entry have been removed.$\r$\n$\r$\nYour saved recovery records were kept so reinstalling will safely resume."
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

!macro Gate PREFIX
Function ${PREFIX}AcquireGate
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\DeadlockSync.Setup") p.r0 ?e'
  Pop $1
  StrCpy $SetupMutex $0
  ${If} $SetupMutex == 0
    SetErrorLevel 2
    MessageBox MB_ICONSTOP "Setup could not start. Please try again." /SD IDOK
    Abort
  ${EndIf}
  ${If} $1 == 183
    Call ${PREFIX}ReleaseGate
    SetErrorLevel 2
    MessageBox MB_ICONINFORMATION "Deadlock Sync setup is already open. Finish or close that window first." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd
Function ${PREFIX}ReleaseGate
  ${If} $SetupMutex != 0
    System::Call 'kernel32::CloseHandle(p $SetupMutex)'
    StrCpy $SetupMutex 0
  ${EndIf}
FunctionEnd
!macroend
!insertmacro Gate ""
!insertmacro Gate "un."

Function .onInit
  ${IfNot} ${AtLeastWin10}
    SetErrorLevel 2
    MessageBox MB_ICONSTOP "Deadlock Sync requires Windows 10 or newer." /SD IDOK
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    SetErrorLevel 2
    MessageBox MB_ICONSTOP "This download requires 64-bit Windows." /SD IDOK
    Abort
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
  StrCpy $INSTDIR "$LOCALAPPDATA\DeadlockSync"
  Call AcquireGate
  StrCpy $AutoStart 1
  StrCpy $NoStart 0
  IfFileExists "$INSTDIR\app\DeadlockSync.exe" 0 new_install
    ReadRegStr $0 HKCU "${RUN_KEY}" "Deadlock Sync"
    ${If} $0 != '$\"$INSTDIR\app\DeadlockSync.exe$\" --background'
      StrCpy $AutoStart 0
    ${EndIf}
  new_install:
  ${GetParameters} $Options
  ClearErrors
  ${GetOptions} $Options "/NOAUTOSTART" $0
  ${IfNot} ${Errors}
    StrCpy $AutoStart 0
  ${EndIf}
  ClearErrors
  ${GetOptions} $Options "/NOSTART" $0
  ${IfNot} ${Errors}
    StrCpy $NoStart 1
  ${EndIf}
FunctionEnd

Function OptionsCreate
  !insertmacro MUI_HEADER_TEXT "Install Deadlock Sync" "A small app in your Windows notification area."
  nsDialogs::Create 1018
  Pop $Dialog
  ${NSD_CreateLabel} 0 0 100% 42u "Live syncing runs while you play. When the game is closed, the app recovers missing history and automatically waits when Steam limits requests."
  Pop $0
  ${NSD_CreateCheckbox} 0 55u 100% 16u "Start Deadlock Sync when I sign in to Windows"
  Pop $CheckStart
  ${If} $AutoStart == 1
    ${NSD_Check} $CheckStart
  ${EndIf}
  ${NSD_CreateLabel} 0 86u 100% 42u "You can stop syncing from the tray menu at any time.$\r$\n$\r$\nClick Install to continue."
  Pop $0
  nsDialogs::Show
FunctionEnd
Function OptionsLeave
  ${NSD_GetState} $CheckStart $AutoStart
FunctionEnd
Function FinishShown
  ${If} $NoStart == 1
    ${NSD_Uncheck} $mui.FinishPage.Run
  ${EndIf}
FunctionEnd
Function LaunchSync
  SetOutPath "$INSTDIR\app"
  Call ReleaseGate
  ClearErrors
  ${If} $HadApp == 1
  ${AndIf} ${FileExists} "$INSTDIR\state\recovery.json"
    ; Opening after an update must not count as an explicit retry for every account.
    Exec '$\"$INSTDIR\app\DeadlockSync.exe$\" --show'
  ${Else}
    Exec '$\"$INSTDIR\app\DeadlockSync.exe$\" --enable --show'
  ${EndIf}
  ${If} ${Errors}
    MessageBox MB_ICONEXCLAMATION "Installation is complete. Open Deadlock Sync from the Start menu to begin." /SD IDOK
  ${EndIf}
FunctionEnd
Function .onGUIEnd
  Call ReleaseGate
FunctionEnd
Function .onInstFailed
  SetErrorLevel 2
FunctionEnd

Section "Install"
  SetDetailsPrint textonly
  DetailPrint "Checking the installation..."
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  File /r "${PAYLOAD}/*"
  ClearErrors
  IfSilent prepare_quiet
    ExecWait '$\"$PLUGINSDIR\payload\DeadlockSync.exe$\" --prepare-install --show-errors' $Result
    Goto prepared
  prepare_quiet:
    ExecWait '$\"$PLUGINSDIR\payload\DeadlockSync.exe$\" --prepare-install' $Result
  prepared:
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "Setup could not check the app. Please close Deadlock Sync and try again." /SD IDOK
    Abort
  ${EndIf}
  ${If} $Result != 0
    Abort "Installation stopped. Your existing app and saved records have not been replaced."
  ${EndIf}
  ClearErrors
  CreateDirectory "$INSTDIR"
  IfErrors copy_failed
  ; A marked new payload is complete even when a later metadata write failed.
  ; An interrupted rename with no current app can safely restore its old app.
  IfFileExists "$INSTDIR\app.previous\*" 0 recover_staging
    IfFileExists "$INSTDIR\app\DeadlockSync.exe" 0 restore_previous
    IfFileExists "$INSTDIR\app\.setup-payload" 0 recovery_needed
    ClearErrors
    RMDir /r "$INSTDIR\app.previous"
    IfErrors cleanup_failed
    Goto recover_staging
  restore_previous:
    IfFileExists "$INSTDIR\app\*" recovery_needed
    IfFileExists "$INSTDIR\app.previous\DeadlockSync.exe" 0 recovery_needed
    ClearErrors
    Rename "$INSTDIR\app.previous" "$INSTDIR\app"
    IfErrors recovery_needed
  recover_staging:
  ; app.new is reserved exclusively for setup; runtime records live in state.
  ClearErrors
  RMDir /r "$INSTDIR\app.new"
  IfFileExists "$INSTDIR\app.new\*" cleanup_failed
  StrCpy $HadApp 0
  DetailPrint "Installing Deadlock Sync..."
  ClearErrors
  CreateDirectory "$INSTDIR\app.new"
  IfErrors copy_failed
  ClearErrors
  CopyFiles /SILENT "$PLUGINSDIR\payload\*.*" "$INSTDIR\app.new"
  IfErrors copy_failed
  WriteINIStr "$INSTDIR\app.new\.setup-payload" "Payload" "Version" "${VERSION}"
  IfErrors copy_failed
  IfFileExists "$INSTDIR\app\*" 0 replace_app
    ClearErrors
    Rename "$INSTDIR\app" "$INSTDIR\app.previous"
    ${If} ${Errors}
      RMDir /r "$INSTDIR\app.new"
      MessageBox MB_ICONSTOP "The app is still in use. Close Deadlock Sync and try setup again." /SD IDOK
      Abort
    ${EndIf}
    StrCpy $HadApp 1
  replace_app:
  ClearErrors
  Rename "$INSTDIR\app.new" "$INSTDIR\app"
  ${If} ${Errors}
    ${If} $HadApp == 1
      ClearErrors
      Rename "$INSTDIR\app.previous" "$INSTDIR\app"
      IfErrors recovery_needed
      RMDir /r "$INSTDIR\app.new"
      MessageBox MB_ICONSTOP "Setup could not finish. The previous app was restored. Close the app and try setup again." /SD IDOK
    ${Else}
      RMDir /r "$INSTDIR\app.new"
      MessageBox MB_ICONSTOP "Setup could not finish installing the app. Close the app and try setup again. Your saved records were kept." /SD IDOK
    ${EndIf}
    Abort
  ${EndIf}
  DetailPrint "Adding the Start menu shortcut..."
  SetOutPath "$INSTDIR\app"
  ClearErrors
  CreateShortcut "$SMPROGRAMS\Deadlock Sync.lnk" "$INSTDIR\app\DeadlockSync.exe" "--enable --show" "$INSTDIR\app\DeadlockSync.exe" 0
  IfErrors metadata_failed
  ${If} $AutoStart == 1
    WriteRegStr HKCU "${RUN_KEY}" "Deadlock Sync" '$\"$INSTDIR\app\DeadlockSync.exe$\" --background'
    IfErrors metadata_failed
  ${Else}
    ReadRegStr $0 HKCU "${RUN_KEY}" "Deadlock Sync"
    ${If} $0 == '$\"$INSTDIR\app\DeadlockSync.exe$\" --background'
      ClearErrors
      DeleteRegValue HKCU "${RUN_KEY}" "Deadlock Sync"
      IfErrors metadata_failed
    ${EndIf}
  ${EndIf}
  ClearErrors
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors metadata_failed
  WriteRegStr HKCU "${PRODUCT_KEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr HKCU "${PRODUCT_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${PRODUCT_KEY}" "Publisher" "mapleroyal"
  WriteRegStr HKCU "${PRODUCT_KEY}" "DisplayIcon" "$INSTDIR\app\DeadlockSync.exe"
  WriteRegStr HKCU "${PRODUCT_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${PRODUCT_KEY}" "URLInfoAbout" "https://github.com/mapleroyal/deadlock-sync"
  WriteRegStr HKCU "${PRODUCT_KEY}" "URLUpdateInfo" "https://github.com/mapleroyal/deadlock-sync/releases/latest"
  WriteRegStr HKCU "${PRODUCT_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${PRODUCT_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU "${PRODUCT_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${PRODUCT_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${PRODUCT_KEY}" "EstimatedSize" ${PAYLOAD_KIB}
  IfErrors metadata_failed
  IfFileExists "$INSTDIR\app.previous\*" 0 previous_removed
  ClearErrors
  RMDir /r "$INSTDIR\app.previous"
  IfErrors cleanup_failed
  previous_removed:
  Delete "$INSTDIR\Uninstall.ps1"
  SetErrorLevel 0
  DetailPrint "Installation complete."
  Goto done
  copy_failed:
    RMDir /r "$INSTDIR\app.new"
    MessageBox MB_ICONSTOP "Setup could not copy the app. Check free disk space and try again. Your saved records have not been changed." /SD IDOK
    Abort
  metadata_failed:
    MessageBox MB_ICONSTOP "The app files were installed, but Windows could not finish adding the shortcut, startup entry or uninstall information. Close the app and run setup again to finish. Your saved records were kept." /SD IDOK
    Abort
  cleanup_failed:
    MessageBox MB_ICONSTOP "Windows could not remove temporary setup files. Close Deadlock Sync and run setup again. If needed, restart Windows first. Your app and saved records were kept." /SD IDOK
    Abort
  recovery_needed:
    MessageBox MB_ICONSTOP "Setup could not safely restore a previous installation. Your files were preserved in $INSTDIR. Close the app and try again after restarting Windows. If this continues, contact support and keep the app.previous folder." /SD IDOK
    Abort
  done:
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  StrCpy $INSTDIR "$LOCALAPPDATA\DeadlockSync"
  Call un.AcquireGate
FunctionEnd
Function un.onGUIEnd
  Call un.ReleaseGate
FunctionEnd
Function un.onUninstFailed
  SetErrorLevel 2
FunctionEnd
Section "Uninstall"
  SetDetailsPrint textonly
  DetailPrint "Closing Deadlock Sync..."
  IfFileExists "$INSTDIR\app\DeadlockSync.exe" 0 remove_files
    ClearErrors
    IfSilent prepare_uninstall_quiet
      ExecWait '$\"$INSTDIR\app\DeadlockSync.exe$\" --prepare-uninstall --show-errors' $Result
      Goto prepared_uninstall
    prepare_uninstall_quiet:
      ExecWait '$\"$INSTDIR\app\DeadlockSync.exe$\" --prepare-uninstall' $Result
    prepared_uninstall:
    ${If} ${Errors}
      MessageBox MB_ICONSTOP "Uninstall could not close the app. Close Deadlock Sync and try again." /SD IDOK
      Abort
    ${EndIf}
    ${If} $Result != 0
      Abort "Uninstall stopped without removing your files."
    ${EndIf}
  remove_files:
  DetailPrint "Removing the app..."
  SetOutPath "$TEMP"
  ReadRegStr $0 HKCU "${RUN_KEY}" "Deadlock Sync"
  ${If} $0 == '$\"$INSTDIR\app\DeadlockSync.exe$\" --background'
    ClearErrors
    DeleteRegValue HKCU "${RUN_KEY}" "Deadlock Sync"
    IfErrors remove_failed
  ${EndIf}
  ClearErrors
  Delete "$SMPROGRAMS\Deadlock Sync.lnk"
  IfErrors remove_failed
  ; Remove only a known superseded backup; keep ambiguous recovery copies.
  IfFileExists "$INSTDIR\app.previous\*" 0 uninstall_payload
    IfFileExists "$INSTDIR\app\.setup-payload" 0 remove_failed
    ClearErrors
    RMDir /r "$INSTDIR\app.previous"
    IfErrors remove_failed
  uninstall_payload:
  RMDir /r "$INSTDIR\app.new"
  IfFileExists "$INSTDIR\app.new\*" remove_failed
  ClearErrors
  !include "${DELETE_FILES}"
  Delete "$INSTDIR\app\.setup-payload"
  IfErrors remove_failed
  ; Nonrecursive directory removal preserves any unrelated files.
  RMDir "$INSTDIR\app\ingest"
  RMDir "$INSTDIR\app"
  ; A retry after an interrupted metadata write may have no registration yet.
  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w "${PRODUCT_KEY}", i 0, i 0x20119, *p.r0) i.r1'
  ${If} $1 == 2
    Goto registration_removed
  ${EndIf}
  ${If} $1 != 0
    Goto remove_failed
  ${EndIf}
  System::Call 'advapi32::RegCloseKey(p r0)'
  ClearErrors
  DeleteRegKey HKCU "${PRODUCT_KEY}"
  IfErrors remove_failed
  registration_removed:
  ClearErrors
  Delete "$INSTDIR\Uninstall.exe"
  ${If} ${Errors}
    ; Keep a retry entry if Windows could not remove the uninstaller itself.
    WriteRegStr HKCU "${PRODUCT_KEY}" "DisplayName" "${PRODUCT}"
    WriteRegStr HKCU "${PRODUCT_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    Goto remove_failed
  ${EndIf}
  RMDir "$INSTDIR"
  SetErrorLevel 0
  DetailPrint "Removed. Your recovery records were kept."
  Goto uninstall_done
  remove_failed:
    MessageBox MB_ICONSTOP "Windows could not finish removing Deadlock Sync. Close the app, then try Uninstall again. If needed, restart Windows first. Your saved recovery records were kept." /SD IDOK
    Abort
  uninstall_done:
SectionEnd
