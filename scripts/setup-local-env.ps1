param(
    [string]$ApiUrl = "http://10.0.0.60:8000"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$serverEnvPath = Join-Path $repoRoot "server\.env"
$appEnvPath = Join-Path $repoRoot "app\.env"

function New-SecureToken {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Read-EnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $line = Get-Content -LiteralPath $Path -Encoding utf8 |
        Where-Object { $_ -match ("^\s*" + [regex]::Escape($Key) + "\s*=") } |
        Select-Object -First 1
    if ($null -eq $line) {
        return $null
    }
    return ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
}

$openAiKey = Read-EnvValue $serverEnvPath "OPENAI_API_KEY"
$demoToken = Read-EnvValue $serverEnvPath "DEMO_TOKEN"
$adminToken = Read-EnvValue $serverEnvPath "ADMIN_TOKEN"
$existingApiUrl = Read-EnvValue $appEnvPath "EXPO_PUBLIC_API_URL"

if ([string]::IsNullOrWhiteSpace($demoToken)) {
    $demoToken = New-SecureToken
}
if ([string]::IsNullOrWhiteSpace($adminToken)) {
    $adminToken = New-SecureToken
}
if (
    -not $PSBoundParameters.ContainsKey("ApiUrl") -and
    -not [string]::IsNullOrWhiteSpace($existingApiUrl)
) {
    $ApiUrl = $existingApiUrl
}
$apiUri = [Uri]$ApiUrl
$lanWebOrigin = "{0}://{1}:8081" -f $apiUri.Scheme, $apiUri.Host

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines(
    $serverEnvPath,
    @(
        "OPENAI_API_KEY=$openAiKey",
        "OPENAI_MODEL=gpt-5.6",
        "OPENAI_DAILY_CALL_LIMIT=3",
        "AUTO_SEED_DEMO_DATA=true",
        "DEMO_TOKEN=$demoToken",
        "ADMIN_TOKEN=$adminToken",
        "CORS_ORIGINS=http://localhost:8081,http://127.0.0.1:8081,$lanWebOrigin"
    ),
    $utf8NoBom
)
[System.IO.File]::WriteAllLines(
    $appEnvPath,
    @(
        "EXPO_PUBLIC_API_URL=$ApiUrl",
        "EXPO_PUBLIC_DEMO_TOKEN=$demoToken"
    ),
    $utf8NoBom
)

[pscustomobject]@{
    ServerEnvCreated = Test-Path -LiteralPath $serverEnvPath -PathType Leaf
    AppEnvConfigured = Test-Path -LiteralPath $appEnvPath -PathType Leaf
    ApiKeyPresent = -not [string]::IsNullOrWhiteSpace($openAiKey)
    DemoTokenPresent = -not [string]::IsNullOrWhiteSpace($demoToken)
    AdminTokenPresent = -not [string]::IsNullOrWhiteSpace($adminToken)
}
