import json
import requests
import xml.etree.ElementTree as ET
from packaging import version
from datetime import date
from spring_boot_mappings import spring_boot_to_framework, spring_boot_to_liquibase

def load_endoflife_data():
    # The newest release has the tag "latest"
    download_url = "https://github.com/HenryJobst/endoflife.json/releases/download/latest/endoflife.json"

    # Fetch the endoflife.json file
    response = requests.get(download_url)
    response.raise_for_status()
    return response.json()

def get_current_date():
    return date.today().isoformat()

def get_supported_versions(product_data):
    current_date = get_current_date()
    return [entry for entry in product_data if entry.get("eol") and entry["eol"] > current_date]

def check_npm_versions(path, eol_data):
    with open(path) as f:
        pkg = json.load(f)
    deps = pkg.get("dependencies", {})
    result = {}
    current_date = get_current_date()
    for dep, ver in deps.items():
        if dep in eol_data and "result" in eol_data[dep] and "releases" in eol_data[dep]["result"]:
            releases = eol_data[dep]["result"]["releases"]
            # Find the latest supported version (not EOL)
            latest_supported = next((v["name"] for v in releases if not v.get("isEol", True)), None)
            result[dep] = {"used": ver, "latest_supported": latest_supported}
        else:
            result[dep] = {"used": ver, "status": "Not checked - dependency not found in endoflife.json"}
    return result

def check_maven_versions(path, eol_data):
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}

    # Extract all properties from the pom.xml
    properties = {}
    for prop in root.findall(".//m:properties/*", ns):
        properties[prop.tag.replace("{http://maven.apache.org/POM/4.0.0}", "")] = prop.text

    result = {}

    # Check parent dependency
    parent = root.find(".//m:parent", ns)
    if parent is not None:
        parent_group = parent.find("m:groupId", ns)
        parent_artifact = parent.find("m:artifactId", ns)
        parent_version = parent.find("m:version", ns)

        if parent_group is not None and parent_artifact is not None and parent_version is not None:
            parent_name = parent_artifact.text.lower()
            parent_group_name = parent_group.text.lower()
            parent_ver = parent_version.text

            # For spring-boot-starter-parent, we need to check against spring-boot
            if parent_name == "spring-boot-starter-parent" and "spring-boot" in eol_data and "result" in eol_data["spring-boot"] and "releases" in eol_data["spring-boot"]["result"]:
                releases = eol_data["spring-boot"]["result"]["releases"]
                supported_versions = [v["name"] for v in releases if not v.get("isEol", True)]
                result[parent_name] = {"used": parent_ver, "supported_versions": supported_versions}

                # Also check spring-framework when spring-boot is used
                if "spring-framework" in eol_data and "result" in eol_data["spring-framework"] and "releases" in eol_data["spring-framework"]["result"]:
                    # Determine Spring Framework version based on Spring Boot version
                    spring_framework_version = None
                    for sb_version, sf_version in spring_boot_to_framework.items():
                        if parent_ver.startswith(sb_version):
                            spring_framework_version = sf_version
                            break

                    # If no mapping found, use the version from properties if available
                    if spring_framework_version is None and "spring-oxm.version" in properties:
                        spring_framework_version = properties["spring-oxm.version"].strip()
                    # Fallback to a default if still not found
                    if spring_framework_version is None:
                        spring_framework_version = "6.2.5"  # Default fallback

                    spring_framework_releases = eol_data["spring-framework"]["result"]["releases"]
                    spring_framework_supported_versions = [v["name"] for v in spring_framework_releases if not v.get("isEol", True)]
                    result["spring-framework"] = {"used": spring_framework_version, "supported_versions": spring_framework_supported_versions}
            else:
                result[parent_name] = {"used": parent_ver, "status": "Not checked - dependency not found in endoflife.json"}

    # Check java version
    if "java.version" in properties and "java" in eol_data and "result" in eol_data["java"] and "releases" in eol_data["java"]["result"]:
        java_version = properties["java.version"]
        releases = eol_data["java"]["result"]["releases"]
        supported_versions = [v["name"] for v in releases if not v.get("isEol", True)]
        result["java"] = {"used": java_version, "supported_versions": supported_versions}

    # Check regular dependencies
    for dep in root.findall(".//m:dependency", ns):
        artifact = dep.find("m:artifactId", ns)
        group_id = dep.find("m:groupId", ns)
        version_el = dep.find("m:version", ns)

        if artifact is not None:
            name = artifact.text.lower()
            group_name = group_id.text.lower() if group_id is not None else ""

            # Handle dependencies with explicit versions
            if version_el is not None:
                ver = version_el.text

                # Resolve property references in version
                if ver and ver.startswith("${"):
                    # Extract property name, handling cases where there might be whitespace or newlines
                    prop_ref = ver.strip()
                    if prop_ref.endswith("}"):
                        prop_name = prop_ref[2:-1]  # Remove ${ and }
                        if prop_name in properties:
                            ver = properties[prop_name].strip()  # Strip whitespace from resolved version

                if name in eol_data and "result" in eol_data[name] and "releases" in eol_data[name]["result"]:
                    releases = eol_data[name]["result"]["releases"]
                    # Get all supported versions (not EOL)
                    supported_versions = [v["name"] for v in releases if not v.get("isEol", True)]
                    result[name] = {"used": ver, "supported_versions": supported_versions}
                else:
                    result[name] = {"used": ver, "status": "Not checked - dependency not found in endoflife.json"}

            # Handle dependencies without explicit versions (managed by parent)
            else:
                # Special handling for known dependencies
                if name == "liquibase-core" and group_name == "org.liquibase":
                    # For liquibase, we need to determine the version based on Spring Boot version
                    # Get the parent Spring Boot version
                    parent_ver = None
                    parent_element = root.find(".//m:parent/m:version", ns)
                    if parent_element is not None:
                        parent_ver = parent_element.text

                    # Determine Liquibase version based on Spring Boot version
                    liquibase_version = None
                    if parent_ver:
                        for sb_version, lq_version in spring_boot_to_liquibase.items():
                            if parent_ver.startswith(sb_version):
                                liquibase_version = lq_version
                                break

                    # Fallback to default if no mapping found
                    if liquibase_version is None:
                        liquibase_version = "4.26.0"  # Default fallback

                    if "liquibase" in eol_data and "result" in eol_data["liquibase"] and "releases" in eol_data["liquibase"]["result"]:
                        releases = eol_data["liquibase"]["result"]["releases"]
                        supported_versions = [v["name"] for v in releases if not v.get("isEol", True)]
                        result["liquibase"] = {"used": liquibase_version, "supported_versions": supported_versions}
    return result

def main():
    eol_data = load_endoflife_data()

    # Get reports
    frontend_report = check_npm_versions("frontend/package.json", eol_data)
    backend_report = check_maven_versions("backend/pom.xml", eol_data)

    # Process frontend dependencies
    frontend_eol = {}
    frontend_unchecked = {}
    frontend_up_to_date = {}

    for dep, info in frontend_report.items():
        if "status" in info:
            # Dependency couldn't be checked
            frontend_unchecked[dep] = info
        elif "latest_supported" in info:
            # Check if the dependency is end-of-life
            # Strip version prefixes like ^ or ~ for npm packages
            used_version = info["used"]
            if used_version.startswith(("^", "~", ">")):
                used_version = used_version[1:]

            # Use packaging.version for proper semantic version comparison
            try:
                if version.parse(used_version) < version.parse(info["latest_supported"]):
                    frontend_eol[dep] = {
                        "used": info["used"],
                        "required": info["latest_supported"]
                    }
                else:
                    # Dependency is up-to-date
                    frontend_up_to_date[dep] = {
                        "used": info["used"]
                    }
            except (TypeError, ValueError):
                # If version parsing fails, fall back to string comparison
                if info["used"] != info["latest_supported"]:
                    frontend_eol[dep] = {
                        "used": info["used"],
                        "required": info["latest_supported"]
                    }
                else:
                    # Dependency is up-to-date
                    frontend_up_to_date[dep] = {
                        "used": info["used"]
                    }
            # If used version matches latest_supported, it's up-to-date

    # Process backend dependencies
    backend_eol = {}
    backend_unchecked = {}
    backend_up_to_date = {}

    for dep, info in backend_report.items():
        if "status" in info:
            # Dependency couldn't be checked
            backend_unchecked[dep] = info
        elif "supported_versions" in info:
            # Check if the dependency is end-of-life
            if info["supported_versions"]:
                # Use packaging.version for proper semantic version comparison
                try:
                    used_ver = version.parse(info["used"])
                    # Check if any supported version is greater than or equal to the used version
                    is_supported = any(version.parse(v) <= used_ver for v in info["supported_versions"])

                    if not is_supported:
                        backend_eol[dep] = {
                            "used": info["used"],
                            "required": info["supported_versions"][0]  # Use first supported version
                        }
                    else:
                        # Dependency is up-to-date
                        backend_up_to_date[dep] = {
                            "used": info["used"]
                        }
                except (TypeError, ValueError):
                    # If version parsing fails, fall back to string comparison
                    if info["used"] not in info["supported_versions"]:
                        backend_eol[dep] = {
                            "used": info["used"],
                            "required": info["supported_versions"][0]  # Use first supported version
                        }
                    else:
                        # Dependency is up-to-date
                        backend_up_to_date[dep] = {
                            "used": info["used"]
                        }
            # If used version is in supported_versions, it's up-to-date

    # Print the reports in a compact, table-like format
    print("=== Frontend ===")
    print("End-of-life dependencies:")
    if frontend_eol:
        print(f"{'Dependency':<30} {'Used Version':<20} {'Required Version':<20}")
        print("-" * 70)
        for dep, info in frontend_eol.items():
            print(f"{dep:<30} {info['used']:<20} {info['required']:<20}")
    else:
        print("None")

    print("\nUp-to-date dependencies:")
    if frontend_up_to_date:
        print(f"{'Dependency':<30} {'Used Version':<20}")
        print("-" * 50)
        for dep, info in frontend_up_to_date.items():
            print(f"{dep:<30} {info['used']:<20}")
    else:
        print("None")

    print("\nUnchecked dependencies:")
    if frontend_unchecked:
        print(f"{'Dependency':<30} {'Used Version':<20}")
        print("-" * 50)
        for dep, info in frontend_unchecked.items():
            print(f"{dep:<30} {info['used']:<20}")
    else:
        print("None")

    print("\n=== Backend ===")
    print("End-of-life dependencies:")
    if backend_eol:
        print(f"{'Dependency':<30} {'Used Version':<20} {'Required Version':<20}")
        print("-" * 70)
        for dep, info in backend_eol.items():
            print(f"{dep:<30} {info['used']:<20} {info['required']:<20}")
    else:
        print("None")

    print("\nUp-to-date dependencies:")
    if backend_up_to_date:
        print(f"{'Dependency':<30} {'Used Version':<20}")
        print("-" * 50)
        for dep, info in backend_up_to_date.items():
            print(f"{dep:<30} {info['used']:<20}")
    else:
        print("None")

    print("\nUnchecked dependencies:")
    if backend_unchecked:
        print(f"{'Dependency':<30} {'Used Version':<20}")
        print("-" * 50)
        for dep, info in backend_unchecked.items():
            print(f"{dep:<30} {info['used']:<20}")
    else:
        print("None")

if __name__ == "__main__":
    main()
