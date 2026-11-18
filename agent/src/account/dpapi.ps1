$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Security
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $entropy = [Text.Encoding]::UTF8.GetBytes('qcal-ziyor-account/v1')
    if ($request.operation -eq 'seal') {
        $plain = [Text.Encoding]::UTF8.GetBytes([string]$request.value)
        $encrypted = [Security.Cryptography.ProtectedData]::Protect($plain, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        @{ value = [Convert]::ToBase64String($encrypted) } | ConvertTo-Json -Compress
        [Array]::Clear($plain, 0, $plain.Length)
    } elseif ($request.operation -eq 'unseal') {
        $encrypted = [Convert]::FromBase64String([string]$request.value)
        $plain = [Security.Cryptography.ProtectedData]::Unprotect($encrypted, $entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser)
        @{ value = [Text.Encoding]::UTF8.GetString($plain) } | ConvertTo-Json -Compress
        [Array]::Clear($plain, 0, $plain.Length)
    } else { throw 'unsupported' }
} catch {
    [Console]::Error.Write('account_protection_failed')
    exit 1
}
