#Requires -Version 5.1

function New-PresenceDirectoryAcl {
    param(
        [Parameter(Mandatory = $true)]
        [Security.Principal.SecurityIdentifier]$PairingSid,
        [switch]$Writable
    )

    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [Security.AccessControl.PropagationFlags]::None
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, 'FullControl', $inherit, $propagation, 'Allow'
        )))
    }
    # The interactive user may exchange pairing files, never replace SYSTEM code.
    $rights = if ($Writable) { 'Modify' } else { 'ReadAndExecute' }
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
        $PairingSid, $rights, $inherit, $propagation, 'Allow'
    )))
    return $acl
}
