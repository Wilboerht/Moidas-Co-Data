function Test-PrivateIPv4([string]$address) {
    $parts = $address.Split(".")
    if ($parts.Length -ne 4) { return $false }
    $a = [int]$parts[0]
    $b = [int]$parts[1]
    return ($a -eq 10) -or
        ($a -eq 192 -and $b -eq 168) -or
        ($a -eq 172 -and $b -ge 16 -and $b -le 31)
}

$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        (Test-PrivateIPv4 $_.IPAddress) -and
        $_.InterfaceAlias -notlike '*VMware*' -and
        $_.InterfaceAlias -notlike '*vEthernet*' -and
        $_.InterfaceAlias -notlike '*WSL*' -and
        $_.InterfaceAlias -notlike '*Loopback*' -and
        $_.InterfaceAlias -notlike '*Tailscale*' -and
        $_.InterfaceAlias -ne 'cfw-tap'
    }
foreach ($address in $addresses) {
    $address.IPAddress
}
