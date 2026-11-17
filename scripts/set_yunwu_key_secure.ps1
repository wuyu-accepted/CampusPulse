$ErrorActionPreference = 'Stop'

$secureValue = Read-Host 'Paste YUNWU_API_KEY (input is hidden)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
try {
    $plainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if (-not $plainValue.StartsWith('sk-') -or $plainValue.Length -lt 20) {
        throw 'The value does not look like the expected API key.'
    }
    [Environment]::SetEnvironmentVariable('YUNWU_API_KEY', $plainValue, 'User')
    Write-Host 'YUNWU_API_KEY is stored in the Windows user environment. No project file was written.'
}
finally {
    if ($null -ne $plainValue) {
        $plainValue = $null
    }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secureValue.Dispose()
}

Read-Host 'Press Enter to close this window'
