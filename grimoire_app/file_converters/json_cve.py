# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Penthertz (Sébastien Dudek)
from grimoire_app.converters import register_handler

def cve_json_to_markdown(json_path: str) -> str:
    """Convert CVE JSON to Markdown format"""
    import json
    with open(json_path, 'r') as f:
        cve_data = json.load(f)
    
    cve_info = cve_data.get('containers', {}).get('cna', {})
    cve_id = cve_data.get('cveMetadata', {}).get('cveId', 'Unknown')
    
    md = f"# {cve_id}\n\n"
    
    if 'title' in cve_info:
        md += f"**Title:** {cve_info['title']}\n\n"
    
    if 'descriptions' in cve_info:
        md += "## Description\n\n"
        for desc in cve_info['descriptions']:
            if desc.get('lang') == 'en':
                md += f"{desc.get('value', '')}\n\n"
    
    if 'affected' in cve_info:
        md += "## Affected Products\n\n"
        for affected in cve_info['affected']:
            vendor = affected.get('vendor', 'Unknown')
            product = affected.get('product', 'Unknown')
            md += f"- **{vendor}/{product}**\n"
            versions = affected.get('versions', [])
            if versions:
                for v in versions:
                    version_str = v.get('version', 'Unknown')
                    if v.get('lessThanOrEqual', {}):
                        version_str += f" (<= {v.get('lessThanOrEqual')})"
                    status = v.get('status', '')
                    md += f"  - `{version_str}` ({status})\n"
            md += "\n"
    
    if 'metrics' in cve_info:
        md += "## Metrics\n\n"
        for metric in cve_info['metrics']:
            cvss_data = metric.get('cvssV3.1', {})
            if cvss_data:
                score = cvss_data.get('baseScore', 'N/A')
                vector = cvss_data.get('vectorString', 'N/A')
                md += f"- **CVSS v3.1 Score:** {score}\n"
                md += f"- **Vector:** `{vector}`\n\n"
            else:
                cvss_data = metric.get('cvssV3_0', {})
                if cvss_data:
                    score = cvss_data.get('baseScore', 'N/A')
                    vector = cvss_data.get('vectorString', 'N/A')
                    md += f"- **CVSS v3.0 Score:** {score}\n"
                    md += f"- **Vector:** `{vector}`\n\n"
    
    if 'references' in cve_info:
        md += "## References\n\n"
        for ref in cve_info['references']:
            url = ref.get('url', '#')
            md += f"- [{url}]({url})\n"
        md += "\n"
    
    metadata = cve_data.get('cveMetadata', {})
    md += "## Metadata\n\n"
    md += f"- **Published:** {metadata.get('datePublished', 'N/A')}\n"
    md += f"- **Updated:** {metadata.get('dateUpdated', 'N/A')}\n"
    
    return md

def handle_json(path: str) -> str:
    """Handle JSON files: only convert if it matches CVE-YYYY-NNNN pattern"""
    # Match CVE-YYYY-NNNN.json pattern (4 digits year, 4+ digits ID)
    import re as _re
    if _re.search(r'CVE-\d{4}-\d{4,}', path):
        return cve_json_to_markdown(path)

register_handler(".json", handle_json)