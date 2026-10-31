// Rules for file-inspector, in the YARA dialect TB's scan library reads.
// Each rule describes something worth a second look, not proof of malware.

rule PowerShell_Download_Cradle : script downloader {
  meta:
    severity = "high"
    description = "PowerShell that downloads and runs code"
  strings:
    $iex = "IEX" nocase fullword
    $invoke = "Invoke-Expression" nocase
    $dl1 = "DownloadString" nocase
    $dl2 = "DownloadFile" nocase
    $wc = "Net.WebClient" nocase
  condition:
    ($iex or $invoke) and ($dl1 or $dl2 or $wc)
}

rule Encoded_PowerShell : script {
  meta:
    severity = "high"
    description = "PowerShell started with a base64-encoded command"
  strings:
    // ascii and wide: also UTF-16 text (Windows event logs, memory dumps)
    $ps = "powershell" ascii wide nocase
    $enc = /-(e|ec|enc|encodedcommand)\s+[A-Za-z0-9+\/=]{20,}/ ascii wide nocase
  condition:
    $ps and $enc
}

rule URL_To_Bare_IP : network {
  meta:
    severity = "medium"
    description = "A web address that points straight at an IP address"
  strings:
    $url = /https?:\/\/[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}[:\/]/
  condition:
    $url
}

rule UPX_Packed_PE : packer {
  meta:
    severity = "medium"
    description = "A Windows program squeezed with the UPX packer"
  strings:
    $upx0 = "UPX0"
    $upx1 = "UPX1"
  condition:
    uint16(0) == 0x5A4D and ($upx0 or $upx1)
}

rule Process_Injection_Imports : windows {
  meta:
    severity = "high"
    description = "Imports the functions used to inject code into other programs"
  strings:
    $a = "VirtualAllocEx"
    $b = "WriteProcessMemory"
    $c = "CreateRemoteThread"
    $d = "NtUnmapViewOfSection"
  condition:
    uint16(0) == 0x5A4D and 2 of them
}

rule Linux_Reverse_Shell_Strings : linux {
  meta:
    severity = "high"
    description = "Strings typical of a Linux reverse shell"
  strings:
    $sh = "/bin/sh"
    $bash = "/bin/bash"
    $dev = "/dev/tcp/"
    $i = "-i" fullword
    $dup = "dup2"
  condition:
    ($sh or $bash) and ($dev or ($dup and $i))
}

rule Ransom_Note_Words : ransomware {
  meta:
    severity = "high"
    description = "Words ransom notes use"
  strings:
    $a = "your files have been encrypted" nocase
    $b = "bitcoin" nocase
    $c = "decrypt" nocase
  condition:
    $a or ($b and $c)
}

rule Shadow_Copy_Deletion : windows {
  meta:
    severity = "high"
    description = "Deletes Windows shadow copies (backups), as ransomware does"
  strings:
    $a = "vssadmin" nocase
    $b = "delete shadows" nocase
    $c = "shadowcopy delete" nocase
  condition:
    $a and ($b or $c)
}
