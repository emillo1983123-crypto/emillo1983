[CmdletBinding()]
param(
    [string]$LanBaseUrl = "",
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$ServiceAddonPath = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$RepositoryId = "repository.subtitle.tts.pl"
$ServiceId = "service.subtitle.tts.pl"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ScriptRoot
$SourceRoot = Join-Path $ScriptRoot "source"
$PublicRoot = Join-Path $ScriptRoot "public"
$BuildParent = Join-Path $ScriptRoot ".build"
$BuildRoot = Join-Path $BuildParent ([System.Guid]::NewGuid().ToString("N"))
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    [System.IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Publish-File {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    # Serwer HTTP otwiera pliki ze wspoldzieleniem odczytu i zapisu. Kopiowanie
    # z nadpisaniem dziala rowniez wtedy, gdy Kodi wlasnie sprawdza indeks.
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function New-KodiZip {
    param(
        [Parameter(Mandatory = $true)][string]$AddonRoot,
        [Parameter(Mandatory = $true)][string]$AddonId,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $ResolvedAddonRoot = (Resolve-Path -LiteralPath $AddonRoot).Path
    $Output = [System.IO.File]::Open(
        $Destination,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $Archive = $null
    try {
        $Archive = New-Object System.IO.Compression.ZipArchive -ArgumentList @(
            $Output,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $false,
            [System.Text.Encoding]::UTF8
        )

        # Jawny wpis katalogu glownego ulatwia Kodi enumeracje archiwum.
        $null = $Archive.CreateEntry("$AddonId/")

        $Directories = Get-ChildItem -LiteralPath $ResolvedAddonRoot -Directory -Recurse -Force |
            Sort-Object FullName
        foreach ($Directory in $Directories) {
            $Relative = $Directory.FullName.Substring($ResolvedAddonRoot.Length).TrimStart('\', '/')
            $EntryName = "$AddonId/" + $Relative.Replace('\', '/') + "/"
            $null = $Archive.CreateEntry($EntryName)
        }

        $Files = Get-ChildItem -LiteralPath $ResolvedAddonRoot -File -Recurse -Force |
            Sort-Object FullName
        foreach ($File in $Files) {
            $Relative = $File.FullName.Substring($ResolvedAddonRoot.Length).TrimStart('\', '/')
            $EntryName = "$AddonId/" + $Relative.Replace('\', '/')
            $Entry = $Archive.CreateEntry(
                $EntryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            )
            $Entry.LastWriteTime = [System.DateTimeOffset]$File.LastWriteTime

            $ShareMode = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
            $Input = [System.IO.File]::Open(
                $File.FullName,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                $ShareMode
            )
            $EntryStream = $null
            try {
                $EntryStream = $Entry.Open()
                $Input.CopyTo($EntryStream)
            }
            finally {
                if ($null -ne $EntryStream) { $EntryStream.Dispose() }
                $Input.Dispose()
            }
        }
    }
    finally {
        if ($null -ne $Archive) { $Archive.Dispose() }
        $Output.Dispose()
    }
}

function Test-KodiZip {
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$ExpectedId,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )

    $Archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        if ($Archive.Entries.Count -eq 0) {
            throw "Puste archiwum ZIP: $ZipPath"
        }

        $Roots = @()
        $ManifestEntry = $null
        foreach ($Entry in $Archive.Entries) {
            $Name = $Entry.FullName
            if (-not $Name) {
                throw "ZIP zawiera wpis bez nazwy: $ZipPath"
            }
            if ($Name.Contains('\')) {
                throw "ZIP zawiera niedozwolony backslash w nazwie: $Name"
            }
            if ($Name.StartsWith('/')) {
                throw "ZIP zawiera sciezke absolutna: $Name"
            }

            $NormalizedName = $Name.TrimEnd('/')
            $Segments = @($NormalizedName.Split('/'))
            if ($Segments.Count -eq 0 -or $Segments[0] -eq "") {
                throw "ZIP zawiera niepoprawna sciezke: $Name"
            }
            if ($Segments -contains "." -or $Segments -contains "..") {
                throw "ZIP zawiera niebezpieczna sciezke: $Name"
            }
            if ($Roots -notcontains $Segments[0]) {
                $Roots += $Segments[0]
            }

            if ($Name -ceq "$ExpectedId/addon.xml") {
                $ManifestEntry = $Entry
            }

            if (-not $Name.EndsWith('/')) {
                # Pelny odczyt wykrywa uszkodzone dane lub bledna kompresje.
                $EntryStream = $Entry.Open()
                try {
                    $EntryStream.CopyTo([System.IO.Stream]::Null)
                }
                finally {
                    $EntryStream.Dispose()
                }
            }
        }

        if ($Roots.Count -ne 1 -or $Roots[0] -cne $ExpectedId) {
            throw "ZIP musi miec dokladnie jeden katalog glowny '$ExpectedId'; znaleziono: $($Roots -join ', ')"
        }
        if ($null -eq $ManifestEntry) {
            throw "ZIP nie zawiera wymaganego pliku $ExpectedId/addon.xml"
        }

        $ManifestStream = $ManifestEntry.Open()
        $Reader = $null
        try {
            $Reader = New-Object System.IO.StreamReader -ArgumentList @(
                $ManifestStream,
                [System.Text.Encoding]::UTF8,
                $true
            )
            [xml]$Manifest = $Reader.ReadToEnd()
        }
        finally {
            if ($null -ne $Reader) { $Reader.Dispose() }
            else { $ManifestStream.Dispose() }
        }

        if ($Manifest.DocumentElement.LocalName -ne "addon" -or
            $Manifest.DocumentElement.GetAttribute("id") -cne $ExpectedId -or
            $Manifest.DocumentElement.GetAttribute("version") -cne $ExpectedVersion) {
            throw "Manifest wewnatrz ZIP-a nie pasuje do $ExpectedId $ExpectedVersion"
        }
    }
    finally {
        $Archive.Dispose()
    }
}

function Get-LanIPv4 {
    $Client = New-Object System.Net.Sockets.UdpClient
    try {
        # Connect wybiera aktywna trase, ale nie wysyla zadnego pakietu UDP.
        $Client.Connect("8.8.8.8", 53)
        $Candidate = ([System.Net.IPEndPoint]$Client.Client.LocalEndPoint).Address.IPAddressToString
        if ($Candidate -and $Candidate -ne "127.0.0.1" -and $Candidate -notmatch '^169\.254\.') {
            return $Candidate
        }
    }
    catch {
        # Komputer moze byc w odizolowanej sieci LAN - probujemy DNS ponizej.
    }
    finally {
        $Client.Dispose()
    }

    $Addresses = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName())
    foreach ($Address in $Addresses) {
        $Candidate = $Address.IPAddressToString
        if ($Address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
            $Candidate -ne "127.0.0.1" -and
            $Candidate -notmatch '^169\.254\.') {
            return $Candidate
        }
    }

    throw "Nie znaleziono adresu IPv4 komputera w sieci LAN. Podaj -LanBaseUrl, np. http://192.168.1.20:8080."
}

function Resolve-ServiceAddonPath {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        $Resolved = Resolve-Path -LiteralPath $RequestedPath -ErrorAction Stop
        return $Resolved.Path
    }

    $Candidates = @(
        (Join-Path $WorkspaceRoot $ServiceId),
        (Join-Path $WorkspaceRoot "kodi_addon\$ServiceId"),
        (Join-Path $WorkspaceRoot "addon\$ServiceId"),
        (Join-Path $WorkspaceRoot "addons\$ServiceId")
    )

    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath (Join-Path $Candidate "addon.xml")) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    throw @"
Nie znaleziono dodatku $ServiceId.
Podaj jego katalog parametrem, np.:
  .\build_repo.ps1 -ServiceAddonPath "C:\sciezka\$ServiceId"
"@
}

function Read-AddonManifest {
    param([Parameter(Mandatory = $true)][string]$AddonRoot)

    $ManifestPath = Join-Path $AddonRoot "addon.xml"
    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        throw "Brak addon.xml w katalogu: $AddonRoot"
    }

    $Raw = [System.IO.File]::ReadAllText($ManifestPath)
    try {
        [xml]$Document = $Raw
    }
    catch {
        throw "Niepoprawny XML w pliku ${ManifestPath}: $($_.Exception.Message)"
    }

    if ($null -eq $Document.DocumentElement -or $Document.DocumentElement.LocalName -ne "addon") {
        throw "Plik $ManifestPath nie zawiera glownego elementu <addon>."
    }

    $Id = $Document.DocumentElement.GetAttribute("id")
    $Version = $Document.DocumentElement.GetAttribute("version")
    if (-not $Id -or -not $Version) {
        throw "Plik $ManifestPath musi zawierac atrybuty id i version."
    }
    if ($Id -notmatch '^[a-z0-9]+(?:[._-][a-z0-9]+)*$') {
        throw "Niebezpieczny lub niepoprawny identyfikator dodatku: $Id"
    }
    if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z.+~-]*$') {
        throw "Niepoprawny numer wersji dodatku ${Id}: $Version"
    }

    return [PSCustomObject]@{
        Id = $Id
        Version = $Version
        Root = $AddonRoot
        ManifestPath = $ManifestPath
        RawManifest = $Raw
        Document = $Document
    }
}

if (-not $LanBaseUrl) {
    $DetectedIp = Get-LanIPv4
    $LanBaseUrl = "http://${DetectedIp}:$Port"
}

$LanBaseUrl = $LanBaseUrl.TrimEnd('/')
try {
    $RepositoryUri = [System.Uri]$LanBaseUrl
}
catch {
    throw "Niepoprawny -LanBaseUrl: $LanBaseUrl"
}
if (-not $RepositoryUri.IsAbsoluteUri -or $RepositoryUri.Scheme -notin @("http", "https")) {
    throw "-LanBaseUrl musi byc pelnym adresem HTTP/HTTPS, np. http://192.168.1.20:8080."
}

$ResolvedServiceAddonPath = Resolve-ServiceAddonPath -RequestedPath $ServiceAddonPath

New-Item -ItemType Directory -Path $BuildParent -Force | Out-Null
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $PublicRoot -Force | Out-Null
$PublicZipsRoot = Join-Path $PublicRoot "zips"
$OldZipsRoot = $null
if ($Clean -and (Test-Path -LiteralPath $PublicZipsRoot)) {
    $OldZipsRoot = Join-Path $PublicRoot (".old-zips-" + [System.Guid]::NewGuid().ToString("N"))
    try {
        Move-Item -LiteralPath $PublicZipsRoot -Destination $OldZipsRoot -ErrorAction Stop
    }
    catch {
        $OldZipsRoot = $null
        Write-Warning "Windows chwilowo uzywa starej paczki. Kontynuuje budowanie i nadpisze aktualne wersje."
    }
}
New-Item -ItemType Directory -Path $PublicZipsRoot -Force | Out-Null

$PreparedRoot = Join-Path $BuildRoot "prepared"
$StagingRoot = Join-Path $BuildRoot "packages"
New-Item -ItemType Directory -Path $PreparedRoot -Force | Out-Null
New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null

# Tworzymy manifest repozytorium z aktualnym adresem LAN bez modyfikowania zrodla.
$RepositoryTemplateRoot = Join-Path $SourceRoot $RepositoryId
$RepositoryTemplate = Join-Path $RepositoryTemplateRoot "addon.xml.template"
if (-not (Test-Path -LiteralPath $RepositoryTemplate)) {
    throw "Brak szablonu repozytorium: $RepositoryTemplate"
}
$PreparedRepositoryRoot = Join-Path $PreparedRoot $RepositoryId
Copy-Item -LiteralPath $RepositoryTemplateRoot -Destination $PreparedRepositoryRoot -Recurse
$PreparedTemplate = Join-Path $PreparedRepositoryRoot "addon.xml.template"
$PreparedManifest = Join-Path $PreparedRepositoryRoot "addon.xml"
$RepositoryXml = [System.IO.File]::ReadAllText($PreparedTemplate).Replace("{{BASE_URL}}", $LanBaseUrl)
Write-Utf8NoBom -Path $PreparedManifest -Text $RepositoryXml
Remove-Item -LiteralPath $PreparedTemplate -Force

$AddonRoots = @($PreparedRepositoryRoot, $ResolvedServiceAddonPath)
$SeenIds = @{}
$BuiltAddons = @()

foreach ($AddonRoot in $AddonRoots) {
    $SourceManifest = Read-AddonManifest -AddonRoot $AddonRoot
    if ($SeenIds.ContainsKey($SourceManifest.Id)) {
        throw "Powtorzony identyfikator dodatku: $($SourceManifest.Id)"
    }
    $SeenIds[$SourceManifest.Id] = $true

    if ($SourceManifest.Id -eq $ServiceId -and $SourceManifest.Version -ne "0.7.1") {
        Write-Warning "Budowana wersja $($SourceManifest.Version), a wersja poczatkowa miala byc 0.7.1. To jest poprawne po opublikowaniu aktualizacji."
    }

    $PackageRoot = Join-Path $StagingRoot $SourceManifest.Id
    Copy-Item -LiteralPath $AddonRoot -Destination $PackageRoot -Recurse

    # Artefakty deweloperskie nigdy nie trafiaja do dodatku instalowanego w Kodi.
    $ExcludedDirectoryNames = @(
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".git", ".github", ".vscode", ".idea", "tests", "test"
    )
    Get-ChildItem -LiteralPath $PackageRoot -Directory -Recurse -Force |
        Where-Object { $_.Name -in $ExcludedDirectoryNames } |
        Sort-Object FullName -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    Get-ChildItem -LiteralPath $PackageRoot -File -Recurse -Force |
        Where-Object {
            $_.Extension -in @(".pyc", ".pyo") -or
            $_.Name -like "test_*.py" -or
            $_.Name -like "*_test.py"
        } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

    $PackagedManifest = Read-AddonManifest -AddonRoot $PackageRoot

    $AddonPublicRoot = Join-Path $PublicZipsRoot $PackagedManifest.Id
    New-Item -ItemType Directory -Path $AddonPublicRoot -Force | Out-Null
    $ZipName = "$($PackagedManifest.Id)-$($PackagedManifest.Version).zip"
    $ZipPath = Join-Path $AddonPublicRoot $ZipName
    $TemporaryZipPath = Join-Path $BuildRoot $ZipName

    New-KodiZip -AddonRoot $PackageRoot -AddonId $PackagedManifest.Id -Destination $TemporaryZipPath
    Test-KodiZip -ZipPath $TemporaryZipPath -ExpectedId $PackagedManifest.Id -ExpectedVersion $PackagedManifest.Version
    Publish-File -Source $TemporaryZipPath -Destination $ZipPath

    # Manifest i grafiki obok ZIP-u ulatwiaja diagnostyke i obsluge metadanych Kodi.
    Copy-Item -LiteralPath $PackagedManifest.ManifestPath -Destination (Join-Path $AddonPublicRoot "addon.xml") -Force
    $AssetNodes = $PackagedManifest.Document.SelectNodes("/addon/extension[@point='xbmc.addon.metadata']/assets/*")
    foreach ($AssetNode in @($AssetNodes)) {
        $RelativeAsset = ([string]$AssetNode.InnerText).Trim().Replace('/', [System.IO.Path]::DirectorySeparatorChar)
        if (-not $RelativeAsset -or [System.IO.Path]::IsPathRooted($RelativeAsset) -or $RelativeAsset -match '(^|[\\/])\.\.([\\/]|$)') {
            continue
        }
        $AssetSource = Join-Path $PackageRoot $RelativeAsset
        if (Test-Path -LiteralPath $AssetSource -PathType Leaf) {
            $AssetDestination = Join-Path $AddonPublicRoot $RelativeAsset
            New-Item -ItemType Directory -Path (Split-Path -Parent $AssetDestination) -Force | Out-Null
            Copy-Item -LiteralPath $AssetSource -Destination $AssetDestination -Force
        }
    }

    $BuiltAddons += [PSCustomObject]@{
        Id = $PackagedManifest.Id
        Version = $PackagedManifest.Version
        ZipName = $ZipName
        ZipPath = $ZipPath
        RawManifest = $PackagedManifest.RawManifest
    }
}

$ManifestBlocks = @()
foreach ($Addon in ($BuiltAddons | Sort-Object Id)) {
    $WithoutDeclaration = $Addon.RawManifest -replace '^\s*<\?xml[^?]*\?>\s*', ''
    # addons.xml musi miec identyczne bajty w Windows i po publikacji przez
    # Git. Stale LF zapobiega zmianie sumy MD5 przez core.autocrlf.
    $Indented = "  " + ($WithoutDeclaration.Trim() -replace "\r?\n", "`n  ")
    $ManifestBlocks += $Indented
}
$AddonsXml = "<?xml version=`"1.0`" encoding=`"UTF-8`" standalone=`"yes`"?>`n<addons>`n"
$AddonsXml += ($ManifestBlocks -join "`n")
$AddonsXml += "`n</addons>`n"
$AddonsXmlPath = Join-Path $PublicRoot "addons.xml"
Write-Utf8NoBom -Path $AddonsXmlPath -Text $AddonsXml

$Md5 = (Get-FileHash -LiteralPath $AddonsXmlPath -Algorithm MD5).Hash.ToLowerInvariant()
Write-Utf8NoBom -Path (Join-Path $PublicRoot "addons.xml.md5") -Text "$Md5`n"

$RepositoryPackage = $BuiltAddons | Where-Object { $_.Id -eq $RepositoryId } | Select-Object -First 1
$BootstrapName = $RepositoryPackage.ZipName
$BootstrapPath = Join-Path $PublicRoot $BootstrapName
$TemporaryBootstrapPath = Join-Path $BuildRoot $BootstrapName
Copy-Item -LiteralPath $RepositoryPackage.ZipPath -Destination $TemporaryBootstrapPath -Force
Publish-File -Source $TemporaryBootstrapPath -Destination $BootstrapPath
$Rows = foreach ($Addon in ($BuiltAddons | Sort-Object Id)) {
    $RelativeUrl = "zips/$($Addon.Id)/$($Addon.ZipName)"
    "      <li><a href=`"$RelativeUrl`">$($Addon.Id) $($Addon.Version)</a></li>"
}
$IndexHtml = @"
<!doctype html>
<html lang="pl">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Lektor PL &mdash; repozytorium Kodi</title>
  </head>
  <body>
    <h1>Lektor PL &mdash; repozytorium Kodi</h1>
    <p>W Kodi zainstaluj najpierw pakiet repozytorium, a potem dodatek Lektor PL z tego repozytorium.</p>
    <p><strong>Pakiet startowy:</strong>
      <a href="$BootstrapName">$BootstrapName</a>
    </p>
    <ul>
$($Rows -join "`r`n")
    </ul>
  </body>
</html>
"@
Write-Utf8NoBom -Path (Join-Path $PublicRoot "index.html") -Text $IndexHtml

Write-Host ""
Write-Host "Repozytorium zbudowane poprawnie." -ForegroundColor Green
Write-Host "Adres LAN:       $LanBaseUrl/"
Write-Host "Pakiet startowy: $LanBaseUrl/zips/$RepositoryId/$($RepositoryPackage.ZipName)"
Write-Host "Indeks Kodi:     $LanBaseUrl/addons.xml"
Write-Host ""
Write-Host "Dodatki:"
foreach ($Addon in ($BuiltAddons | Sort-Object Id)) {
    Write-Host "  - $($Addon.Id) $($Addon.Version)"
}

try {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction Stop
}
catch {
    Write-Warning "Nie udalo sie od razu usunac katalogu tymczasowego: $BuildRoot"
}
if ($null -ne $OldZipsRoot) {
    try {
        Remove-Item -LiteralPath $OldZipsRoot -Recurse -Force -ErrorAction Stop
    }
    catch {
        Write-Warning "Stare paczki przeniesiono do $OldZipsRoot, ale Windows jeszcze nie pozwolil ich usunac."
    }
}
