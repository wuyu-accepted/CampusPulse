[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$HostAlias = '10.47.203.235',

    [string]$PublicKeyPath = (Join-Path $env:USERPROFILE '.ssh\id_rsa.pub')
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $PublicKeyPath -PathType Leaf)) {
    throw "SSH public key not found: $PublicKeyPath"
}

$publicKey = (Get-Content -LiteralPath $PublicKeyPath -Raw).Trim()
if ($publicKey -notmatch '^(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp(?:256|384|521))\s+[A-Za-z0-9+/=]+(?:\s+.*)?$') {
    throw 'The selected file is not a supported OpenSSH public key.'
}

Write-Host "Installing only the public key on SSH host '$HostAlias'."
Write-Host 'OpenSSH will request the server password interactively; the password is never stored by this script.'

# The remote command is constant. The public key travels over stdin and is
# appended only when the exact line is not already present.
$publicKey | & ssh `
    -o BatchMode=no `
    -o PubkeyAuthentication=no `
    -o PasswordAuthentication=yes `
    $HostAlias `
    'umask 077; mkdir -p "$HOME/.ssh"; touch "$HOME/.ssh/authorized_keys"; while IFS= read -r key; do grep -qxF "$key" "$HOME/.ssh/authorized_keys" || printf "%s\n" "$key" >> "$HOME/.ssh/authorized_keys"; done'

if ($LASTEXITCODE -ne 0) {
    throw "SSH public-key installation failed with exit code $LASTEXITCODE."
}

& ssh -o BatchMode=yes -o ConnectTimeout=8 $HostAlias 'pwd'
if ($LASTEXITCODE -ne 0) {
    throw 'The key was sent, but non-interactive verification failed.'
}

Write-Host 'Public-key login verified. No password was written to disk.'
