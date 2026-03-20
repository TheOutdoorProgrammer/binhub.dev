#!/usr/bin/env python3
"""
Binary Hub Processor

Processes YAML files to download binaries and upload them to cloud storage.
Generates hierarchical JSON API files and static index.
"""

import json
import hashlib
import tempfile
import zipfile
import tarfile
import atexit
from pathlib import Path
from typing import Dict, List, Set
import yaml
import requests
from posthog import Posthog
from dataclasses import dataclass
from collections import defaultdict

# PostHog analytics and error tracking
posthog = Posthog(
    'phc_aur20epnEcOsmKpTpdbPMjJSzM5ypEtSD4zLwm0Q0aD',
    host='https://g.theoutdoorprogrammer.com',
)
atexit.register(posthog.flush)


@dataclass
class BinaryInfo:
    name: str
    description: str
    homepage: str
    repository: str
    license: str
    version: str
    architectures: Dict[str, Dict[str, str]]
    tags: List[str]


class BinaryProcessor:
    def __init__(self, binaries_dir: str = "binaries", output_dir: str = "output"):
        self.binaries_dir = Path(binaries_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.processed_binaries = []
    
    def normalize_arch(self, arch: str) -> str:
        """Keep full architecture name to preserve OS information."""
        return arch
    
    def get_binary_extension(self, arch: str, binary_name: str) -> str:
        """Get the appropriate file extension for the binary."""
        if arch.startswith("windows"):
            return ".exe"
        return ""
    
    def validate_sha256(self, file_path: Path, expected_hash: str) -> bool:
        """Validate file SHA256 hash."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest() == expected_hash
    
    def extract_binary(self, archive_path: Path, binary_path: str, archive_type: str, output_file: Path) -> None:
        """Extract binary from archive to specific output path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            if archive_type == "zip":
                with zipfile.ZipFile(archive_path, 'r') as zip_file:
                    zip_file.extractall(temp_path)
            elif archive_type in ["tar.gz", "tgz"]:
                with tarfile.open(archive_path, 'r:gz') as tar_file:
                    tar_file.extractall(temp_path)
            elif archive_type == "tar.xz":
                with tarfile.open(archive_path, 'r:xz') as tar_file:
                    tar_file.extractall(temp_path)
            elif archive_type == "tar":
                with tarfile.open(archive_path, 'r') as tar_file:
                    tar_file.extractall(temp_path)
            else:
                raise ValueError(f"Unsupported archive type: {archive_type}")
            
            # Find the binary in the extracted files
            binary_file = temp_path / binary_path
            if not binary_file.exists():
                raise FileNotFoundError(f"Binary not found at {binary_path} in archive")
            
            # Copy to output directory
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(binary_file.read_bytes())
            output_file.chmod(0o755)  # Make executable
    
    def create_nested_path(self, binary_name: str, version: str, arch: str) -> Path:
        """Create nested directory path: {first_letter}/{binary_name}/{version}/{arch_normalized}/"""
        first_char = binary_name[0].lower()
        normalized_arch = self.normalize_arch(arch)
        return self.output_dir / first_char / binary_name / version / normalized_arch
    
    def download_binary(self, binary_info: BinaryInfo, arch: str, arch_data: Dict) -> Dict:
        """Download and process binary into nested directory structure."""
        print(f"Downloading {binary_info.name} {binary_info.version} for {arch}...")
        
        url = arch_data['url']
        binary_type = arch_data['type']
        binary_path = arch_data.get('binary_path_in_archive')
        sha256 = arch_data.get('sha256')
        
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Create nested directory path
        nested_dir = self.create_nested_path(binary_info.name, binary_info.version, arch)
        nested_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine binary filename with extension
        extension = self.get_binary_extension(arch, binary_info.name)
        binary_filename = f"{binary_info.name}{extension}"
        output_file = nested_dir / binary_filename
        
        # Create temp file for download
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            for chunk in response.iter_content(chunk_size=8192):
                temp_file.write(chunk)
            temp_path = Path(temp_file.name)
        
        try:
            # Validate SHA256 if provided
            if sha256 and not self.validate_sha256(temp_path, sha256):
                # Calculate actual hash for debugging
                actual_sha256 = hashlib.sha256()
                with open(temp_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        actual_sha256.update(chunk)
                actual_hash = actual_sha256.hexdigest()
                raise ValueError(f"{binary_info.name}: Expected SHA256 {sha256} got {actual_hash}")
            
            if binary_type == "raw":
                # Raw binary - just copy to output
                output_file.write_bytes(temp_path.read_bytes())
                output_file.chmod(0o755)
            else:
                # Archive - extract binary
                if not binary_path:
                    raise ValueError(f"binary_path_in_archive required for {binary_type} archives")
                self.extract_binary(temp_path, binary_path, binary_type, output_file)
            
            # Return path info for API
            first_char = binary_info.name[0].lower()
            normalized_arch = self.normalize_arch(arch)
            return {
                'url': f"/{first_char}/{binary_info.name}/{binary_info.version}/{normalized_arch}/{binary_filename}",
                'size': output_file.stat().st_size,
                'sha256': sha256
            }
        
        finally:
            temp_path.unlink()  # Clean up temp file
    
    def process_yaml_file(self, yaml_file: Path) -> BinaryInfo:
        """Process a single YAML file."""
        print(f"Processing {yaml_file}...")
        
        with open(yaml_file, 'r') as f:
            data = yaml.safe_load(f)
        
        binary_info = BinaryInfo(
            name=data['name'],
            description=data['description'],
            homepage=data['homepage'],
            repository=data['repository'],
            license=data['license'],
            version=data['version'],
            architectures=data['architectures'],
            tags=data['tags']
        )
        
        # Process each architecture
        processed_architectures = {}
        for arch, arch_data in binary_info.architectures.items():
            try:
                arch_info = self.download_binary(binary_info, arch, arch_data)
                normalized_arch = self.normalize_arch(arch)
                processed_architectures[normalized_arch] = arch_info
            except Exception as e:
                print(f"Error processing {binary_info.name} {arch}: {e}")
                continue
        
        binary_info.architectures = processed_architectures
        return binary_info
    
    def find_yaml_files(self) -> List[Path]:
        """Find all YAML files in the binaries directory."""
        yaml_files = []
        for subdir in self.binaries_dir.iterdir():
            if subdir.is_dir() and subdir.name != ".git":
                for file in subdir.iterdir():
                    if file.suffix.lower() in ['.yaml', '.yml'] and file.name != '.gitkeep':
                        yaml_files.append(file)
        return sorted(yaml_files)
    
    def generate_hierarchical_apis(self, binaries: List[BinaryInfo]) -> None:
        """Generate hierarchical API structure."""
        # Group binaries by first letter
        letter_groups = defaultdict(list)
        for binary in binaries:
            first_char = binary.name[0].lower()
            letter_groups[first_char].append(binary)
        
        # Generate root API - just lists available letters
        root_api = {
            'version': '1.0',
            'directories': sorted(letter_groups.keys())
        }
        
        root_api_file = self.output_dir / 'api.json'
        with open(root_api_file, 'w') as f:
            json.dump(root_api, f, indent=2)
        print(f"Generated root API: {root_api_file}")
        
        # Generate letter-level APIs
        for letter, letter_binaries in letter_groups.items():
            letter_dir = self.output_dir / letter
            letter_dir.mkdir(exist_ok=True)
            
            letter_api = {
                'version': '1.0',
                'binaries': sorted(list(set(b.name for b in letter_binaries)))
            }
            
            letter_api_file = letter_dir / 'api.json'
            with open(letter_api_file, 'w') as f:
                json.dump(letter_api, f, indent=2)
            print(f"Generated letter API: {letter_api_file}")
            
            # Group by binary name for this letter
            binary_groups = defaultdict(list)
            for binary in letter_binaries:
                binary_groups[binary.name].append(binary)
            
            # Generate binary-level APIs
            for binary_name, binary_versions in binary_groups.items():
                binary_dir = letter_dir / binary_name
                binary_dir.mkdir(exist_ok=True)
                
                binary_api = {
                    'name': binary_name,
                    'description': binary_versions[0].description,  # Assume same across versions
                    'homepage': binary_versions[0].homepage,
                    'repository': binary_versions[0].repository,
                    'license': binary_versions[0].license,
                    'tags': binary_versions[0].tags,
                    'versions': sorted(list(set(b.version for b in binary_versions)))
                }
                
                binary_api_file = binary_dir / 'api.json'
                with open(binary_api_file, 'w') as f:
                    json.dump(binary_api, f, indent=2)
                print(f"Generated binary API: {binary_api_file}")
                
                # Generate version-level APIs
                for binary in binary_versions:
                    version_dir = binary_dir / binary.version
                    version_dir.mkdir(exist_ok=True)
                    
                    version_api = {
                        'name': binary.name,
                        'version': binary.version,
                        'architectures': binary.architectures
                    }
                    
                    version_api_file = version_dir / 'api.json'
                    with open(version_api_file, 'w') as f:
                        json.dump(version_api, f, indent=2)
                    print(f"Generated version API: {version_api_file}")
    
    def generate_static_html(self, binaries: List[BinaryInfo]) -> None:
        """Generate static HTML index file."""
        binary_count = len(binaries)
        
        # Generate binary list HTML
        binary_list_html = '<div class="arch-list">'
        for binary in binaries:
            arch_count = len(binary.architectures)
            binary_list_html += f'''
                <div class="arch-item">
                    <strong>{binary.name}</strong><br>
                    <small>{binary.description}</small><br>
                    <small>v{binary.version} - {arch_count} architectures</small>
                </div>
            '''
        binary_list_html += '</div>'
        
        # Create code examples as separate variables to preserve formatting
        quick_start_example = """# Download the latest version of a binary
curl -L https://binhub.dev/j/jq/1.6/linux-amd64/jq -o jq
chmod +x jq

# Download GitHub CLI
curl -L https://binhub.dev/g/gh/2.40.1/linux-amd64/gh -o gh
chmod +x gh"""
        
        directory_example = """/{{first_letter}}/{{binary_name}}/{{version}}/{{os-architecture}}/{{binary}}

Examples:
/j/jq/1.6/linux-amd64/jq
/g/gh/2.40.1/darwin-arm64/gh
/k/kubectl/1.28.0/windows-amd64/kubectl.exe"""
        
        html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BinHub - Universal Binary Distribution</title>
    <script>
      !function(t,e){{var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){{function g(t,e){{var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){{t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){{var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e}},u.people.toString=function(){{return u.toString(1)+".people (stub)"}},o="init capture register register_once register_for_session unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset isFeatureEnabled onFeatureFlags getFeatureFlag getFeatureFlagPayload reloadFeatureFlags group updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey identify setPersonProperties".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])}},e.__SV=1)}}(document,window.posthog||[]);
      posthog.init('phc_aur20epnEcOsmKpTpdbPMjJSzM5ypEtSD4zLwm0Q0aD', {{api_host: 'https://g.theoutdoorprogrammer.com'}});
    </script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f8f9fa;
        }}
        .header {{
            text-align: center;
            margin-bottom: 40px;
            padding: 40px 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
        }}
        .header h1 {{ margin: 0; font-size: 3em; }}
        .header p {{ margin: 10px 0 0 0; font-size: 1.2em; opacity: 0.9; }}
        .section {{
            background: white;
            padding: 30px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .section h2 {{
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }}
        .usage-example {{
            background: #f1f2f6;
            border: 1px solid #ddd;
            border-radius: 5px;
            padding: 15px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            overflow-x: auto;
            white-space: pre-wrap;
        }}
        .arch-list {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .arch-item {{
            background: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            text-align: center;
        }}
        .binary-count {{
            font-size: 2em;
            font-weight: bold;
            color: #3498db;
            margin: 20px 0;
            text-align: center;
        }}
        .api-endpoint {{
            background: #e8f5e8;
            border: 1px solid #4CAF50;
            border-radius: 5px;
            padding: 10px;
            font-family: monospace;
            color: #2e7d32;
            margin: 5px 0;
        }}
        .footer {{
            text-align: center;
            margin-top: 40px;
            padding: 20px;
            color: #666;
            border-top: 1px solid #eee;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🗂️ BinHub</h1>
        <p>Universal Binary Distribution Platform</p>
    </div>

    <div class="section">
        <h2>📋 What is BinHub?</h2>
        <p>BinHub is a centralized platform for distributing pre-compiled binaries across multiple architectures. 
        We host popular command-line tools and utilities, making them easily accessible for automated deployments, 
        CI/CD pipelines, and development environments.</p>
        
        <div class="binary-count">
            {binary_count} binaries available
        </div>
    </div>

    <div class="section">
        <h2>🚀 Quick Start</h2>
        <p>Download binaries directly using curl or wget:</p>
        <pre class="usage-example">{quick_start_example}</pre>
    </div>

    <div class="section">
        <h2>📡 Hierarchical API</h2>
        <p>Discover binaries through our hierarchical API structure:</p>
        <div class="api-endpoint">GET /api.json</div>
        <p>Lists all available first letters/numbers</p>
        
        <div class="api-endpoint">GET /j/api.json</div>
        <p>Lists all binaries starting with 'j'</p>
        
        <div class="api-endpoint">GET /j/jq/api.json</div>
        <p>Shows jq metadata and available versions</p>
        
        <div class="api-endpoint">GET /j/jq/1.6/api.json</div>
        <p>Shows architectures and download URLs for jq v1.6</p>
    </div>

    <div class="section">
        <h2>🏗️ Directory Structure</h2>
        <p>Binaries are organized in a predictable nested structure:</p>
        <pre class="usage-example">{directory_example}</pre>
    </div>

    <div class="section">
        <h2>🤝 Contributing</h2>
        <p>Want to add a binary to BinHub? It's easy!</p>
        <ol>
            <li>Fork the <a href="https://github.com/apollorion/binhub.dev">repository</a></li>
            <li>Add a YAML file in the appropriate directory (e.g., <code>binaries/g/gh.yaml</code>)</li>
            <li>Follow the schema with download URLs, checksums, and metadata</li>
            <li>Submit a pull request</li>
        </ol>
    </div>

    <div class="section">
        <h2>📚 Available Binaries</h2>
        {binary_list_html}
    </div>

    <div class="footer">
        <p>BinHub - Making binary distribution simple and reliable</p>
        <p><a href="https://github.com/apollorion/binhub.dev">Source Code</a> | Open Source | MIT License</p>
    </div>
</body>
</html>'''
        
        html_file = self.output_dir / 'index.html'
        with open(html_file, 'w') as f:
            f.write(html_content)
        
        print(f"Generated HTML file: {html_file}")
    
    def process_all(self) -> None:
        """Process all YAML files."""
        yaml_files = self.find_yaml_files()
        print(f"Found {len(yaml_files)} YAML files to process")
        
        processed_binaries = []
        for yaml_file in yaml_files:
            try:
                binary_info = self.process_yaml_file(yaml_file)
                processed_binaries.append(binary_info)
            except Exception as e:
                print(f"Error processing {yaml_file}: {e}")
                posthog.capture(distinct_id='binhub-processor', event='processing_error', properties={
                    'error': str(e),
                    'yaml_file': str(yaml_file),
                })
                continue
        
        print(f"Successfully processed {len(processed_binaries)} binaries")
        
        # Generate hierarchical APIs
        self.generate_hierarchical_apis(processed_binaries)
        
        # Generate static HTML
        self.generate_static_html(processed_binaries)
        
        return processed_binaries


if __name__ == "__main__":
    processor = BinaryProcessor()
    try:
        posthog.capture(distinct_id='binhub-processor', event='processing_started')
        results = processor.process_all()
        posthog.capture(distinct_id='binhub-processor', event='processing_completed', properties={
            'binaries_count': len(results) if results else 0,
        })
    except Exception as e:
        posthog.capture_exception(e, distinct_id='binhub-processor')
        raise