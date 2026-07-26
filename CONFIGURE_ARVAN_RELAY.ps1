$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root 'mahanbot.env'

function Read-SecretText([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

$relayUrl = Read-Host 'Relay URL [https://otp.mahanvip.ir]'
if ([string]::IsNullOrWhiteSpace($relayUrl)) { $relayUrl = 'https://otp.mahanvip.ir' }
$relayUrl = $relayUrl.Trim().TrimEnd('/')
$botKey = Read-SecretText 'Paste MahanBot key'
$phoneKey = Read-SecretText 'Paste phone device key'

if ($relayUrl -notmatch '^https://') { throw 'Relay URL must start with https://' }
if ($botKey.Length -lt 32) { throw 'MahanBot key is too short.' }
if ($phoneKey.Length -lt 32) { throw 'Phone device key is too short.' }

$values = [ordered]@{}
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $parts = $line -split '=', 2
        $values[$parts[0].Trim()] = $parts[1].Trim()
    }
}
$values['MAHAN_OTP_RELAY_URL'] = $relayUrl
$values['MAHAN_OTP_BOT_KEY'] = $botKey
$values['MAHAN_OTP_PHONE_KEY'] = $phoneKey
$values['MAHAN_OTP_WAIT_TTL_SECONDS'] = '600'
$values['MAHAN_OTP_LONG_POLL_SECONDS'] = '20'

$lines = @('# MahanBot unified local configuration', '# Keep this file private.')
foreach ($key in ($values.Keys | Sort-Object)) { $lines += "$key=$($values[$key])" }
[IO.File]::WriteAllLines($EnvFile, $lines, [Text.UTF8Encoding]::new($false))
Write-Host 'Relay configuration saved. Run START_MAHANBOT.cmd.' -ForegroundColor Green
