import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings


class CupsError(RuntimeError):
    pass


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_OPTION = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_PAGE_RANGES = re.compile(r"^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")


def _run(args, *, timeout=None):
    env = os.environ.copy()
    if settings.CUPS_SERVER:
        env["CUPS_SERVER"] = settings.CUPS_SERVER
    try:
        result = subprocess.run(
            args, capture_output=True, text=True,
            timeout=timeout or settings.CUPS_COMMAND_TIMEOUT,
            check=False, env=env,
        )
    except FileNotFoundError as exc:
        raise CupsError(f"CUPS command not installed: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CupsError(f"{args[0]} did not finish before the timeout.") from exc
    if result.returncode:
        raise CupsError((result.stderr or result.stdout or "CUPS command failed").strip())
    return result.stdout.strip()


def _validate_name(value, label="value"):
    if not value or not SAFE_NAME.fullmatch(value):
        raise CupsError(f"Invalid {label}.")


def parse_option_lines(text):
    options = {}
    for line in text.splitlines():
        match = re.match(r"^([^/\s]+)(?:/[^:]+)?:\s*(.*)$", line.strip())
        if not match:
            continue
        name, raw_values = match.groups()
        values, selected = [], ""
        for value in raw_values.split():
            is_selected = value.startswith("*")
            clean = value.lstrip("*").split("/", 1)[0]
            values.append(clean)
            if is_selected:
                selected = clean
        options[name] = {"choices": values, "selected": selected}
    return options


def _suggest_queue_name(uri):
    parsed = urlparse(uri)
    source = unquote(parsed.hostname or parsed.path.rsplit("/", 1)[-1] or "printer")
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", source).strip("-.")
    return (name or "printer")[:127]


def parse_devices(text):
    """Parse `lpinfo -v` output into safe data for the discovery UI."""
    devices = []
    seen = set()
    for raw_line in text.splitlines():
        parts = raw_line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        kind, uri = parts
        if kind not in {"network", "direct", "serial"} or uri in seen:
            continue
        scheme = urlparse(uri).scheme.lower()
        if not scheme or scheme in {"file", "http", "https"}:
            continue
        seen.add(uri)
        queue_name = _suggest_queue_name(uri)
        driver = "everywhere" if scheme in {"ipp", "ipps", "dnssd"} else "raw"
        label = unquote(urlparse(uri).hostname or queue_name)
        devices.append({
            "kind": kind,
            "uri": uri,
            "scheme": scheme,
            "name": queue_name,
            "description": label,
            "driver": driver,
            "driverless": driver == "everywhere",
        })
    return devices


IPP_ATTRIBUTES = (
    "printer-name", "printer-info", "printer-location", "printer-make-and-model",
    "printer-state", "media-default", "media-supported", "media-source-supported",
    "sides-default", "sides-supported", "print-color-mode-default",
    "print-color-mode-supported", "print-quality-default", "print-quality-supported",
    "printer-resolution-default", "printer-resolution-supported",
    "orientation-requested-default", "orientation-requested-supported",
    "print-scaling-default", "print-scaling-supported", "copies-default",
    "copies-supported", "output-bin-default", "output-bin-supported",
    "finishings-default", "finishings-supported", "media-col-default",
    "document-format-default", "document-format-preferred", "document-format-supported",
)


def parse_ipp_attributes(output, uri=""):
    raw = {}
    for line in output.splitlines():
        match = re.match(r"^\s*([a-z][a-z0-9-]+)\s+\([^)]*\)\s*=\s*(.*?)\s*$", line)
        if match:
            raw[match.group(1)] = match.group(2)

    def values(attribute):
        return [value.strip() for value in raw.get(attribute, "").split(",") if value.strip()]

    specs = (
        ("media", "media-supported", "media-default"),
        ("media-source", "media-source-supported", "media-source-default"),
        ("sides", "sides-supported", "sides-default"),
        ("print-color-mode", "print-color-mode-supported", "print-color-mode-default"),
        ("print-quality", "print-quality-supported", "print-quality-default"),
        ("printer-resolution", "printer-resolution-supported", "printer-resolution-default"),
        ("orientation-requested", "orientation-requested-supported", "orientation-requested-default"),
        ("print-scaling", "print-scaling-supported", "print-scaling-default"),
        ("output-bin", "output-bin-supported", "output-bin-default"),
        ("finishings", "finishings-supported", "finishings-default"),
    )
    options = {}
    for option_name, supported_attr, default_attr in specs:
        choices = values(supported_attr)
        if not choices:
            continue
        selected = raw.get(default_attr, "")
        if option_name == "orientation-requested":
            choices = [{"portrait": "3", "landscape": "4"}.get(choice, choice) for choice in choices]
            selected = {"portrait": "3", "landscape": "4"}.get(selected, selected)
        options[option_name] = {"choices": choices, "selected": selected}

    media_col_default = raw.get("media-col-default", "")
    media_source_default = re.search(r"(?:^|\s)media-source=([^\s}]+)", media_col_default)
    if media_source_default and "media-source" in options:
        options["media-source"]["selected"] = media_source_default.group(1)

    return {
        "name": raw.get("printer-name", ""),
        "queue_name": re.sub(r"[^A-Za-z0-9_.-]+", "-", raw.get("printer-name", "")).strip("-.")[:127] or _suggest_queue_name(uri),
        "description": raw.get("printer-info", ""),
        "location": raw.get("printer-location", ""),
        "model": raw.get("printer-make-and-model", ""),
        "state": raw.get("printer-state", ""),
        "options": options,
        "defaults": {name: spec["selected"] for name, spec in options.items() if spec.get("selected")},
        "document_formats": values("document-format-supported"),
        "preferred_document_format": raw.get("document-format-preferred", ""),
    }


def inspect_ipp_printer(uri, *, timeout=None):
    template = "/usr/share/cups/ipptool/get-printer-attributes.test"
    output = _run(["ipptool", "-tv", uri, template], timeout=timeout)
    return parse_ipp_attributes(output, uri)


def printer_availability(printer):
    """Return a small, UI-safe live status without changing printer state."""
    try:
        identity = inspect_ipp_printer(
            printer.device_uri,
            timeout=settings.CUPS_STATUS_TIMEOUT,
        )
    except CupsError as exc:
        return {"connected": False, "state": "offline", "label": "Offline", "detail": str(exc)}
    state = str(identity.get("state") or "idle").lower()
    if state in {"5", "stopped"}:
        return {"connected": True, "state": "stopped", "label": "Needs attention", "detail": "The printer is reachable but stopped."}
    if state in {"4", "processing"}:
        return {"connected": True, "state": "processing", "label": "Printing", "detail": "Connected and processing a job."}
    return {"connected": True, "state": "ready", "label": "Ready", "detail": "Connected and ready."}


def printer_availabilities(printers):
    """Probe multiple printers concurrently so offline devices do not stack delays."""
    if not printers:
        return []
    with ThreadPoolExecutor(max_workers=min(len(printers), 8)) as executor:
        statuses = executor.map(printer_availability, printers)
    return [
        {"printer": printer, **status}
        for printer, status in zip(printers, statuses)
    ]


def discover_printers():
    # The bundled CUPS service has host networking so mDNS reaches it. It
    # publishes a small cache through the private shared socket directory.
    discovery_file = getattr(settings, "CUPS_DISCOVERY_FILE", "")
    if discovery_file:
        try:
            uris = Path(discovery_file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise CupsError("Network discovery is starting; try again in a few seconds.") from exc
        return parse_devices("\n".join(f"network {uri.strip()}" for uri in uris if uri.strip()))
    return parse_devices(_run(["lpinfo", "-v"]))


def probe_printer(uri, driver="everywhere"):
    """Read live capabilities and defaults directly from the printer over IPP."""
    if uri not in {device["uri"] for device in discover_printers()}:
        raise CupsError("The printer is no longer present in network discovery.")
    return inspect_ipp_printer(uri)


def configure_printer(printer):
    _validate_name(printer.name, "printer name")
    args = ["lpadmin", "-p", printer.name, "-v", printer.device_uri, "-m", printer.driver]
    if printer.description:
        args += ["-D", printer.description]
    if printer.location:
        args += ["-L", printer.location]
    args += ["-o", f"printer-is-shared={'true' if printer.is_shared else 'false'}"]
    for key, value in printer.default_options.items():
        _validate_name(str(key), "option name")
        args += ["-o", f"{key}={value}"]
    _run(args)
    _run(["cupsenable" if printer.is_enabled else "cupsdisable", printer.name])
    if printer.is_enabled:
        _run(["cupsaccept", printer.name])
    if printer.is_default:
        _run(["lpadmin", "-d", printer.name])


def sync_options(printer):
    _validate_name(printer.name, "printer name")
    return parse_option_lines(_run(["lpoptions", "-p", printer.name, "-l"]))


def _resolution_dpi(options):
    match = re.match(r"^(\d+)", str(options.get("printer-resolution", "600")))
    return min(max(int(match.group(1)) if match else 600, 72), 1200)


def _convert_pdf(path, document_format, options, page_ranges=""):
    """Render a PDF to a format accepted natively by a driverless printer."""
    if page_ranges and not SAFE_PAGE_RANGES.fullmatch(page_ranges):
        raise CupsError("Invalid page range.")
    if document_format == "image/pwg-raster":
        device, suffix = "pwgraster", ".pwg"
    elif document_format == "image/urf":
        is_mono = options.get("print-color-mode") == "monochrome"
        device, suffix = ("urfgray" if is_mono else "urf"), ".urf"
    else:
        raise CupsError(f"No PDF conversion is available for {document_format}.")

    temporary = tempfile.NamedTemporaryFile(prefix="print-", suffix=suffix, delete=False)
    temporary.close()
    output_path = Path(temporary.name)
    args = [
        "gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", f"-sDEVICE={device}",
        f"-r{_resolution_dpi(options)}", f"-sOutputFile={output_path}",
    ]
    if options.get("print-color-mode") == "monochrome" and device == "pwgraster":
        args += ["-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray"]
    if page_ranges:
        args.append(f"-sPageList={page_ranges}")
    args.append(str(path))
    try:
        _run(args, timeout=settings.CUPS_CONVERSION_TIMEOUT)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise CupsError("PDF conversion produced an empty print document.")
        return output_path
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _select_document_format(printer):
    formats = list(printer.document_formats or [])
    preferred = printer.preferred_document_format
    if not formats:
        identity = inspect_ipp_printer(printer.device_uri)
        formats = identity.get("document_formats", [])
        preferred = identity.get("preferred_document_format", "")
    # Honour the printer's preferred format first. Driverless CUPS queues can
    # expose only that direct pass-through MIME type even when the device itself
    # advertises additional raster formats.
    candidates = (preferred, "application/pdf", "image/urf", "image/pwg-raster")
    for candidate in candidates:
        if candidate in formats:
            return candidate
    supported = ", ".join(formats) or "unknown"
    raise CupsError(f"The printer does not accept PDF, PWG Raster, or URF (reported: {supported}).")


def _ipp_value(value, label):
    value = str(value)
    if not value or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        raise CupsError(f"Invalid IPP {label}.")
    return value


def _ipp_job_lines(*, copies, options):
    quality = {"draft": "3", "normal": "4", "high": "5"}
    lines = [
        f"ATTR integer copies {int(copies)}",
    ]
    attribute_types = {
        "sides": "keyword", "media": "keyword", "media-source": "keyword",
        "print-color-mode": "keyword", "print-scaling": "keyword",
        "output-bin": "keyword", "orientation-requested": "enum",
        "printer-resolution": "resolution",
    }
    for name, ipp_type in attribute_types.items():
        value = options.get(name)
        if value not in (None, ""):
            lines.append(f"ATTR {ipp_type} {name} {_ipp_value(value, name)}")
    if options.get("print-quality") in quality:
        lines.append(f"ATTR enum print-quality {quality[options['print-quality']]}")
    return lines


def _direct_ipp_request(*, printer, source_path, title, copies, document_format, options, operation):
    job_lines = _ipp_job_lines(copies=copies, options=options)
    safe_title = re.sub(r"[\x00-\x1f\x7f]+", " ", str(title))
    safe_title = safe_title.replace("\\", "\\\\").replace('"', '\\"')
    body = [
        "{", f'NAME "{operation}"', f"OPERATION {operation}",
        "GROUP operation-attributes-tag", "ATTR charset attributes-charset utf-8",
        "ATTR language attributes-natural-language en", f"ATTR uri printer-uri {printer.device_uri}",
        "ATTR name requesting-user-name pdf-manager", f'ATTR name job-name "{safe_title}"',
        "ATTR boolean ipp-attribute-fidelity true",
    ]
    if operation != "Create-Job":
        body.append(f"ATTR mimeMediaType document-format {_ipp_value(document_format, 'document format')}")
    body += ["GROUP job-attributes-tag", *job_lines]
    if operation == "Print-Job":
        escaped_path = str(source_path).replace("\\", "\\\\").replace('"', '\\"')
        body.append(f'FILE "{escaped_path}"')
    body += ["STATUS successful-ok", "}"]
    template = tempfile.NamedTemporaryFile(mode="w", suffix=".test", prefix="ipp-job-", delete=False)
    try:
        template.write("\n".join(body))
        template.close()
        return _run(
            ["ipptool", "-tv", printer.device_uri, template.name],
            timeout=settings.CUPS_CONVERSION_TIMEOUT,
        )
    finally:
        Path(template.name).unlink(missing_ok=True)


def _ipp_job_operation(*, printer, job_uri, operation, source_path=None, document_format=None):
    body = [
        "{", f'NAME "{operation}"', f"OPERATION {operation}",
        "GROUP operation-attributes-tag", "ATTR charset attributes-charset utf-8",
        "ATTR language attributes-natural-language en", f"ATTR uri printer-uri {printer.device_uri}",
        f"ATTR uri job-uri {job_uri}", "ATTR name requesting-user-name pdf-manager",
    ]
    if operation == "Send-Document":
        body += [
            f"ATTR mimeMediaType document-format {_ipp_value(document_format, 'document format')}",
            "ATTR boolean last-document true",
        ]
        escaped_path = str(source_path).replace("\\", "\\\\").replace('"', '\\"')
        body.append(f'FILE "{escaped_path}"')
    elif operation == "Get-Job-Attributes":
        body.append("ATTR keyword requested-attributes all")
    body += ["STATUS successful-ok", "}"]
    template = tempfile.NamedTemporaryFile(mode="w", suffix=".test", prefix="ipp-operation-", delete=False)
    try:
        template.write("\n".join(body))
        template.close()
        return _run(["ipptool", "-tv", printer.device_uri, template.name], timeout=settings.CUPS_CONVERSION_TIMEOUT)
    finally:
        Path(template.name).unlink(missing_ok=True)


def _effective_ipp_options(output):
    effective = {}
    names = (
        "copies", "sides", "media", "print-color-mode", "print-quality",
        "printer-resolution", "orientation-requested", "print-scaling", "output-bin",
    )
    for name in names:
        match = re.search(rf"^\s*{re.escape(name)}\s+\([^)]*\)\s*=\s*(.*?)\s*$", output, re.MULTILINE)
        if match:
            effective[name] = match.group(1)
    media_col = re.search(r"^\s*media-col\s+\([^)]*\)\s*=\s*(.*?)\s*$", output, re.MULTILINE)
    if media_col:
        source = re.search(r"media-source=([^\s}]+)", media_col.group(1))
        if source:
            effective["media-source"] = source.group(1)
    return effective


def _verify_effective_options(requested, effective):
    orientation = {"3": "portrait", "4": "landscape", "5": "reverse-landscape", "6": "reverse-portrait"}
    checks = {
        "sides": requested.get("sides"),
        "media": requested.get("media"),
        "print-color-mode": requested.get("print-color-mode"),
        "print-quality": requested.get("print-quality"),
        "printer-resolution": requested.get("printer-resolution"),
        "orientation-requested": orientation.get(str(requested.get("orientation-requested")), requested.get("orientation-requested")),
        "print-scaling": requested.get("print-scaling"),
        "output-bin": requested.get("output-bin"),
    }
    mismatches = []
    for name, expected in checks.items():
        actual = effective.get(name)
        if expected not in (None, "") and actual is not None and str(actual) != str(expected):
            mismatches.append(f"{name}: requested {expected}, printer stored {actual}")
    requested_source = requested.get("media-source")
    actual_source = effective.get("media-source")
    # 'auto' can legitimately resolve to the currently loaded physical tray.
    if requested_source not in (None, "", "auto") and actual_source and actual_source != requested_source:
        mismatches.append(f"media-source: requested {requested_source}, printer stored {actual_source}")
    if mismatches:
        raise CupsError("The printer changed requested options before printing: " + "; ".join(mismatches))


def certify_printer(printer):
    """Non-printing certification of advertised duplex modes and defaults."""
    document_format = _select_document_format(printer)
    sides_spec = printer.supported_options.get("sides", {})
    modes = sides_spec.get("choices") or [printer.default_options.get("sides", "one-sided")]
    report = {"transport": "ipp-create-send", "document_format": document_format, "sides": {}}
    for mode in modes:
        options = {**printer.default_options, "sides": mode}
        job_uri = ""
        try:
            _direct_ipp_request(
                printer=printer, source_path="", title=f"Certification {mode}", copies=1,
                document_format=document_format, options=options, operation="Validate-Job",
            )
            created = _direct_ipp_request(
                printer=printer, source_path="", title=f"Certification {mode}", copies=1,
                document_format=document_format, options=options, operation="Create-Job",
            )
            uri_match = re.search(r"job-uri \(uri\) = (\S+)", created)
            if not uri_match:
                raise CupsError("Printer did not return the certification job URI.")
            job_uri = uri_match.group(1)
            stored = _ipp_job_operation(printer=printer, job_uri=job_uri, operation="Get-Job-Attributes")
            effective = _effective_ipp_options(stored)
            _verify_effective_options(options, effective)
            report["sides"][mode] = {"verified": True, "effective": effective.get("sides")}
        except CupsError as exc:
            report["sides"][mode] = {"verified": False, "error": str(exc)}
        finally:
            if job_uri:
                try:
                    _ipp_job_operation(printer=printer, job_uri=job_uri, operation="Cancel-Job")
                except CupsError:
                    pass
    return report


def submit_pdf(*, printer, path, title, copies=1, page_ranges="", options=None):
    _validate_name(printer.name, "printer name")
    if not Path(path).is_file():
        raise CupsError("The PDF is no longer available.")
    merged = {**printer.default_options, **(options or {})}
    document_format = _select_document_format(printer)
    converted_path = None
    try:
        source_path = Path(path)
        if document_format != "application/pdf":
            converted_path = _convert_pdf(source_path, document_format, merged, page_ranges)
            source_path = converted_path
        # Validate directly against the printer. The classic CUPS IPP backend
        # silently replaced duplex with one-sided for pass-through URF jobs.
        _direct_ipp_request(
            printer=printer, source_path=source_path, title=title, copies=copies,
            document_format=document_format, options=merged, operation="Validate-Job",
        )
        created = _direct_ipp_request(
            printer=printer, source_path=source_path, title=title, copies=copies,
            document_format=document_format, options=merged, operation="Create-Job",
        )
        job_id = re.search(r"job-id \(integer\) = (\d+)", created)
        job_uri = re.search(r"job-uri \(uri\) = (\S+)", created)
        if not job_id or not job_uri:
            raise CupsError("The printer created a job without returning its identifier.")
        try:
            stored = _ipp_job_operation(printer=printer, job_uri=job_uri.group(1), operation="Get-Job-Attributes")
            effective = _effective_ipp_options(stored)
            _verify_effective_options(merged, effective)
            _ipp_job_operation(
                printer=printer, job_uri=job_uri.group(1), operation="Send-Document",
                source_path=source_path, document_format=document_format,
            )
        except Exception:
            try:
                _ipp_job_operation(printer=printer, job_uri=job_uri.group(1), operation="Cancel-Job")
            except CupsError:
                pass
            raise
        accepted = f"IPP job {job_id.group(1)} accepted by {printer.name}"
        return accepted, merged, effective
    finally:
        if converted_path:
            converted_path.unlink(missing_ok=True)
