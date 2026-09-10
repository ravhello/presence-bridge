#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'installer-access.ps1')

$user = New-Object Security.Principal.SecurityIdentifier('S-1-5-21-1-2-3-1001')
foreach ($writable in @($false, $true)) {
    $acl = New-PresenceDirectoryAcl -PairingSid $user -Writable:$writable
    if (-not $acl.AreAccessRulesProtected) { throw 'ACL must not inherit broad permissions' }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 3) { throw 'Only SYSTEM, Administrators and the pairing user may have access' }
    $rule = $rules | Where-Object { $_.IdentityReference -eq $user }
    $hasModify = ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Modify) -eq [Security.AccessControl.FileSystemRights]::Modify
    if ($hasModify -ne $writable) { throw 'Only the pairing data directory may be writable' }
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $admin = $rules | Where-Object { $_.IdentityReference.Value -eq $sid }
        if ($admin.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {
            throw 'SYSTEM and Administrators must retain full control'
        }
    }
}
Write-Output 'PASS: protected, localized-account-safe ACLs; pairing cannot replace SYSTEM code'
