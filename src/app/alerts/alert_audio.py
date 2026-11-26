"""One Windows-session audio lane shared by all independent popup processes.

The synthesizer child owns the named mutex for the entire synchronous utterance.
Closing/crashing its Python parent cannot release the lock while it still speaks.
No network calls, packages, or paid speech service are involved.
"""


def speech_script(parent_pid):
    return r'''
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding;
$alertSpeechText = [Console]::In.ReadToEnd();
$alertSpeechParentId = PARENT_ID;
$alertSpeechMutex = [System.Threading.Mutex]::new($false, 'Local\XingYunShe.PopupAudio.v1');
$alertSpeechAcquired = $false;
$alertSpeaker = $null;
try {
    $alertSpeechParent = Get-Process -Id $alertSpeechParentId -ErrorAction SilentlyContinue;
    if (-not $alertSpeechParent) { exit 0 };
    $alertSpeechParentStart = $alertSpeechParent.StartTime;
    $alertSpeechDeadline = [DateTime]::UtcNow.AddSeconds(120);
    while (-not $alertSpeechAcquired -and [DateTime]::UtcNow -lt $alertSpeechDeadline) {
        $alertSpeechParent = Get-Process -Id $alertSpeechParentId -ErrorAction SilentlyContinue;
        if (-not $alertSpeechParent -or $alertSpeechParent.StartTime -ne $alertSpeechParentStart) { exit 0 };
        try { $alertSpeechAcquired = $alertSpeechMutex.WaitOne(250) }
        catch [System.Threading.AbandonedMutexException] { $alertSpeechAcquired = $true };
    };
    if (-not $alertSpeechAcquired) { exit 0 };
    $alertSpeechParent = Get-Process -Id $alertSpeechParentId -ErrorAction SilentlyContinue;
    if (-not $alertSpeechParent -or $alertSpeechParent.StartTime -ne $alertSpeechParentStart) { exit 0 };
    # BEGIN POPUP AUDIO: preserve the user's original Windows notification sound.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class XingYunAlertSound {
    [DllImport("winmm.dll", CharSet = CharSet.Unicode)]
    public static extern bool PlaySound(string name, IntPtr module, uint flags);
    [DllImport("user32.dll")]
    public static extern bool MessageBeep(uint type);
}
'@;
    if (-not [XingYunAlertSound]::PlaySound('SystemNotification', [IntPtr]::Zero, 0x10000)) {
        [void][XingYunAlertSound]::MessageBeep(0x40);
    };
    if (-not [string]::IsNullOrWhiteSpace($alertSpeechText)) {
        Add-Type -AssemblyName System.Speech;
        $alertSpeaker = New-Object System.Speech.Synthesis.SpeechSynthesizer;
        $alertVoice = $alertSpeaker.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1;
        if ($alertVoice) { $alertSpeaker.SelectVoice($alertVoice.VoiceInfo.Name) };
        $alertSpeaker.Rate = 1;
        $alertSpeaker.Volume = 100;
        $alertSpeaker.Speak($alertSpeechText);
    };
} finally {
    try { if ($alertSpeaker) { $alertSpeaker.Dispose() } }
    finally {
        if ($alertSpeechAcquired) { $alertSpeechMutex.ReleaseMutex() };
        $alertSpeechMutex.Dispose();
    };
};
'''.replace('PARENT_ID', str(int(parent_pid)))
