/*
   A corpus of rules written in the style of public rule sets (signature-base,
   YARA-Rules): meta blocks, ascii/wide/nocase strings, xor and base64, hex
   jumps, regexes, string counts and offsets, for-loops, private and global
   rules, and the pe, elf and math modules. Written for TB's tests; no rule is
   copied from a third-party set. Every rule must parse; tests/lib/test_yara.py
   also checks what each one matches.
*/

import "pe"
import "elf"
import "math"

global rule Global_Reasonable_Size {
   meta:
      description = "Skip anything huge (a global rule gates every other rule)"
   condition:
      filesize < 20MB
}

private rule IsPE {
   condition:
      uint16(0) == 0x5A4D and uint32(uint32(0x3C)) == 0x00004550
}

private rule IsELF {
   condition:
      uint32be(0) == 0x7F454C46
}

rule SUSP_PowerShell_EncodedCommand : powershell t1059_001 {
   meta:
      description = "PowerShell started with a long base64 command, in ASCII or UTF-16"
      author = "TB tests"
      date = "2026-10-01"
      score = 70
   strings:
      $ps = "powershell" ascii wide nocase
      $enc = /-e(nc|ncodedcommand)?\s+[A-Za-z0-9+\/=]{40,}/ ascii wide nocase
   condition:
      $ps and $enc
}

rule MAL_XOR_Encoded_DOS_Banner {
   meta:
      description = "A PE banner hidden with a one-byte XOR key (the plain text doesn't count)"
      reference = "https://yara.readthedocs.io/en/stable/writingrules.html#xor-strings"
   strings:
      $banner = "This program cannot be run in DOS mode" xor(0x01-0xff)
   condition:
      $banner
}

rule HKTL_Credential_Dumper_Strings : hacktool {
   meta:
      description = "Words credential dumping tools print"
      score = 80
   strings:
      $s1 = "sekurlsa::logonpasswords" ascii wide nocase
      $s2 = "lsadump::sam" ascii wide nocase
      $s3 = "privilege::debug" ascii wide nocase
      $w1 = "dumper" fullword nocase
   condition:
      IsPE and 2 of ($s*) or (all of ($s*) and $w1)
}

rule SUSP_Base64_Download_Cradle {
   meta:
      description = "Download-and-run words that were base64 encoded (ASCII or UTF-16)"
   strings:
      $a = "DownloadString" base64
      $b = "Invoke-Expression" base64 base64wide
   condition:
      any of them
}

rule SUSP_Custom_Base64_Alphabet {
   meta:
      description = "Config text encoded with a shuffled base64 alphabet"
   strings:
      $cfg = "beacon_interval" base64("ZYXWVUTSRQPONMLKJIHGFEDCBAzyxwvutsrqponmlkjihgfedcba9876543210+/")
   condition:
      $cfg
}

rule APT_Shellcode_Hex_Patterns {
   meta:
      description = "Classic x86 shellcode prologues"
   strings:
      // call $+5 style getpc, then a stack frame
      $getpc = { FC E8 ?? 00 00 00 [0-8] 60 89 E5 }
      /* PEB access through fs:[0x30] */
      $peb = { 64 8B ( 15 | 0D | 35 ) 30 00 00 00 }
      $nib = { 4? 5A ~00 [2-] 50 45 00 00 4C 01 }
   condition:
      $getpc or $peb or $nib
}

rule MAL_PE_Known_Imphash {
   meta:
      description = "Matches one import table exactly"
      hash = "ff791ac2ab72535efaec8d5d96c0270e"
   condition:
      pe.imphash() == "ff791ac2ab72535efaec8d5d96c0270e"
}

rule SUSP_PE_UPX_Sections {
   meta:
      description = "Section names the UPX packer leaves"
   condition:
      pe.is_pe and for any i in (0 .. pe.number_of_sections - 1) : (
         pe.sections[i].name == "UPX0" or pe.sections[i].name == "UPX1"
      )
}

rule SUSP_PE_Writable_Code_Section {
   meta:
      description = "A section that is both writable and executable"
   condition:
      for any section in pe.sections : (
         section.characteristics & pe.SECTION_MEM_WRITE and section.characteristics & pe.SECTION_MEM_EXECUTE
      )
}

rule SUSP_PE_High_Entropy {
   meta:
      description = "The whole file looks compressed or encrypted"
   condition:
      IsPE and math.entropy(0, filesize) >= 7.0
}

rule SUSP_PE_Injection_Imports {
   meta:
      description = "Imports the trio used to inject code into another process"
   condition:
      pe.imports("kernel32.dll", "VirtualAllocEx") and pe.imports("KERNEL32.DLL", "writeprocessmemory")
      and pe.imports(/kernel32/i, /Thread$/) >= 1
}

rule SUSP_PE_Service_DLL {
   meta:
      description = "A DLL exporting ServiceMain (runs as a Windows service)"
   condition:
      pe.is_dll() and pe.exports("ServiceMain") and pe.number_of_exports >= 1
}

rule SUSP_PE_Signed_By_Demo_CA {
   meta:
      description = "Signed with a certificate issued by a test CA"
   condition:
      pe.number_of_signatures > 0 and for any sig in pe.signatures : (
         sig.issuer contains "Demo CA" and sig.serial == "12:34" and sig.not_after > 1700000000
      )
}

rule SUSP_PE_GUI_Without_Imports {
   meta:
      description = "A GUI program with no import table at all"
   condition:
      pe.subsystem == pe.SUBSYSTEM_WINDOWS_GUI and pe.number_of_imports == 0
}

rule INFO_PE_Built_With_MSVC {
   meta:
      description = "The Rich header names a linker build"
   condition:
      pe.rich_signature.toolid(259) >= 5 and pe.rich_signature.version(30795) > 0
}

rule SUSP_PE_Entrypoint_Code {
   meta:
      description = "A push ebp / mov ebp, esp prologue right at the entry point"
   strings:
      $prologue = { 55 8B EC }
   condition:
      $prologue at pe.entry_point
}

rule SUSP_PE_Large_Overlay {
   condition:
      pe.overlay.size > 10000 and pe.overlay.offset + pe.overlay.size == filesize
}

rule LNX_Reverse_Shell : linux {
   meta:
      description = "Strings a /dev/tcp reverse shell needs"
   strings:
      $sh = "/bin/sh" fullword
      $tcp = /\/dev\/tcp\/[0-9.]+\/[0-9]+/
   condition:
      IsELF and elf.type == elf.ET_EXEC and $sh and $tcp
}

rule LNX_Writable_Executable_Segment {
   condition:
      for any seg in elf.segments : (
         seg.type == elf.PT_LOAD and seg.flags & elf.PF_W and seg.flags & elf.PF_X
      )
}

rule LNX_UPX_Section_Name {
   condition:
      elf.number_of_sections > 0 and for any i in (0 .. elf.number_of_sections - 1) : (
         elf.sections[i].name contains "UPX"
      )
}

rule SUSP_Many_Bare_IP_URLs {
   meta:
      description = "More than three URLs that point straight at an IP address"
   strings:
      $url = /https?:\/\/[0-9]{1,3}(\.[0-9]{1,3}){3}[:\/]/
   condition:
      #url > 3
}

rule SUSP_MZ_Header_Offsets {
   strings:
      $mz = "MZ"
      $pe = { 50 45 00 00 }
   condition:
      $mz at 0 and @pe[1] < 1024 and !pe[1] == 4
}

rule SUSP_Ransom_Note {
   meta:
      description = "Phrases ransom notes use"
   strings:
      $r1 = "your files have been encrypted" nocase
      $r2 = "bitcoin" nocase fullword
      $r3 = "decrypt" nocase
      $r4 = ".onion" nocase
   condition:
      filesize < 64KB and 3 of ($r*)
}

rule SUSP_Half_Of_Recon_Commands {
   strings:
      $c1 = "whoami /all" nocase
      $c2 = "net group \"domain admins\"" nocase
      $c3 = "nltest /dclist" nocase
      $c4 = "ipconfig /all" nocase
   condition:
      50% of ($c*)
}

rule SUSP_Repeated_Shell_Names {
   strings:
      $ = "cmd.exe" nocase
      $ = "powershell.exe" nocase
   condition:
      for any of them : ( # >= 2 )
}

rule SUSP_Script_In_First_KB {
   strings:
      $a = "<script" nocase
      $b = "eval(" nocase
   condition:
      any of them in (0 .. 1024)
}

rule SUSP_Onion_Address_Wide {
   strings:
      $onion = /[a-z2-7]{16,56}\.onion/ wide nocase
   condition:
      $onion
}

rule SUSP_Arithmetic_And_Bits {
   meta:
      description = "Exercises the condition operators (always true on files over 200 bytes)"
   condition:
      filesize \ 2 > 100 and (filesize % 2 == 0 or filesize - 1 > 0)
      and (0xFF & 0x0F) << 1 == 0x1E and ~0 == -1 and -(1) < 0 and 2 * 3 + 1 == 7
      and 0x10 >> 4 == 1 and (5 | 2) ^ 1 == 6
}

rule SUSP_Defined_Fields {
   condition:
      defined pe.number_of_sections and pe.number_of_sections > 2
      and for any n in (1, 2, 3, 4) : ( n == pe.number_of_sections )
}

rule SUSP_Byte_Statistics {
   condition:
      filesize > 512 and math.mean(0, filesize) < 200 and math.deviation(0, filesize, 127.5) > 10
      and math.in_range(math.serial_correlation(0, filesize), -1.0, 1.0)
      and math.count(0x00) > 10 and math.percentage(0x00) > 0.1 and math.mode() == 0
}

rule SUSP_String_Operators {
   condition:
      pe.is_pe and pe.sections[0].name startswith ".te" and pe.sections[0].name iendswith "XT"
      and pe.sections[0].name icontains "tex" and pe.sections[0].name iequals ".TEXT"
      and pe.sections[0].name matches /^\.text$/ and not (pe.sections[0].name endswith "data")
}
