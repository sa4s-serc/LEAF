"""Simulation export functionality for LEAF-Cloud.

This module provides functions to export simulation results to various formats
including JSON, YAML, CSV, and HTML. It handles serialization of different
data types and provides options for customizing the output format and styling.
"""
from __future__ import annotations

import csv
import json
import logging
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any, Dict, List, Optional, Union, TypeVar, Type, Sequence, 
    Mapping, IO, cast, overload
)

import yaml

from ..exceptions import LeafCloudError, ExportError

# Initialize module logger
logger = logging.getLogger(__name__)

# Type variables for generic types
T = TypeVar('T')
DictLike = Union[Dict[str, Any], Mapping[str, Any]]


class ExportFormat(str, Enum):
    """Supported export formats for simulation results.
    
    Attributes:
        JSON: JavaScript Object Notation format
        YAML: YAML Ain't Markup Language format
        CSV: Comma-Separated Values format
        HTML: HyperText Markup Language format with table
    """
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"
    HTML = "html"

    @classmethod
    def from_string(cls, format_str: str) -> ExportFormat:
        """Convert a string to an ExportFormat enum.
        
        Args:
            format_str: The format as a string (case-insensitive).
                
        Returns:
            The corresponding ExportFormat enum.
            
        Raises:
            ValueError: If the format string is not supported.
        """
        try:
            return cls(format_str.lower())
        except ValueError as e:
            raise ValueError(
                f"Unsupported export format: {format_str}. "
                f"Supported formats: {', '.join(f.value for f in cls)}"
            ) from e


def _serialize_for_export(data: Any) -> Any:
    """Recursively serialize data for export.
    
    Handles common non-serializable types and provides meaningful string
    representations.
    
    Args:
        data: The data to serialize.
        
    Returns:
        A serializable version of the data.
    """
    if data is None or isinstance(data, (str, int, float, bool)):
        return data
    elif isinstance(data, (list, tuple)):
        return [_serialize_for_export(item) for item in data]
    elif isinstance(data, dict):
        return {str(k): _serialize_for_export(v) for k, v in data.items()}
    elif hasattr(data, 'to_dict'):
        return _serialize_for_export(data.to_dict())
    elif hasattr(data, '__dict__'):
        return _serialize_for_export(data.__dict__)
    else:
        return str(data)


def _export_to_json(data: Any, **kwargs: Any) -> str:
    """Export data to JSON format.
    
    Args:
        data: The data to export.
        **kwargs: Additional arguments for json.dumps()
        
    Returns:
        The exported data as a JSON string.
    """
    kwargs.setdefault('indent', 2)
    kwargs.setdefault('default', str)
    return json.dumps(_serialize_for_export(data), **kwargs)


def _export_to_yaml(data: Any, **kwargs: Any) -> str:
    """Export data to YAML format.
    
    Args:
        data: The data to export.
        **kwargs: Additional arguments for yaml.dump()
        
    Returns:
        The exported data as a YAML string.
    """
    kwargs.setdefault('default_flow_style', False)
    kwargs.setdefault('allow_unicode', True)
    return yaml.dump(_serialize_for_export(data), **kwargs)


def _export_to_csv(data: Sequence[DictLike], **kwargs: Any) -> str:
    """Export data to CSV format.
    
    Args:
        data: A sequence of dictionary-like objects to export.
        **kwargs: Additional arguments for csv.DictWriter
        
    Returns:
        The exported data as a CSV string.
        
    Raises:
        ValueError: If data is not a sequence of dictionary-like objects.
    """
    if not isinstance(data, (list, tuple)) or not data:
        return ""
    
    if not all(isinstance(item, (dict, Mapping)) for item in data):
        raise ValueError("CSV export requires a sequence of dictionary-like objects")
    
    # Convert to list of dicts for easier processing
    dicts = [dict(item) for item in data]
    
    # Get all unique field names
    fieldnames = sorted({
        field for item in dicts 
        for field in item.keys()
    })
    
    # Convert to CSV
    output = []
    writer = csv.DictWriter(
        output, 
        fieldnames=fieldnames,
        **{k: v for k, v in kwargs.items() 
           if k in ('dialect', 'extrasaction', 'restval', 'delimiter', 'quotechar')}
    )
    
    writer.writeheader()
    writer.writerows(dicts)
    return '\n'.join(output)


def _export_to_html(
    data: Sequence[DictLike], 
    title: str = "Simulation Results",
    **kwargs: Any
) -> str:
    """Export data to HTML format.
    
    Args:
        data: A sequence of dictionary-like objects to export.
        title: The title for the HTML page.
        **kwargs: Additional styling options:
            - table_style: CSS styles for the table
            - header_style: CSS styles for table headers
            - cell_style: CSS styles for table cells
            - row_style: CSS styles for table rows
            - even_row_style: CSS styles for even rows
            - odd_row_style: CSS styles for odd rows
            
    Returns:
        The exported data as an HTML string.
        
    Raises:
        ValueError: If data is not a sequence of dictionary-like objects.
    """
    if not isinstance(data, (list, tuple)) or not data:
        return "<table></table>"
    
    if not all(isinstance(item, (dict, Mapping)) for item in data):
        raise ValueError("HTML export requires a sequence of dictionary-like objects")
    
    # Convert to list of dicts for easier processing
    dicts = [dict(item) for item in data]
    
    # Get all unique field names
    fieldnames = list({
        field for item in dicts 
        for field in item.keys()
    })
    
    # Default styles
    styles = {
        'table': 'border-collapse: collapse; width: 100%;',
        'header': 'background-color: #f2f2f2; font-weight: bold;',
        'cell': 'border: 1px solid #ddd; padding: 8px; text-align: left;',
        'row': '',
        'even_row': 'background-color: #f9f9f9;',
        'odd_row': '',
        **{k: v for k, v in kwargs.items() if k.endswith('_style')}
    }
    
    # Generate rows
    rows = []
    for i, item in enumerate(dicts):
        row_class = 'even' if i % 2 == 0 else 'odd'
        row_style = styles['even_row'] if i % 2 == 0 else styles['odd_row']
        
        cells = []
        for field in fieldnames:
            value = item.get(field, '')
            if hasattr(value, '__dict__'):
                value = str(value)
            cells.append(
                f'<td style="{styles["cell"]}">{value}</td>'
            )
        
        rows.append(
            f'<tr style="{styles["row"]} {row_style}">{"".join(cells)}</tr>'
        )
    
    # Generate the HTML
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        table {{{styles['table']}}}
        th {{{styles['header']} {styles['cell']}}}
        td {{{styles['cell']}}}
        tr:hover {{background-color: #f5f5f5;}}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <table>
        <thead>
            <tr>{"".join(f'<th style="{styles["header"]} {styles["cell"]}">{field}</th>' 
                         for field in fieldnames)}</tr>
        </thead>
        <tbody>
            {"".join(rows)}
        </tbody>
    </table>
</body>
</html>"""


@overload
def export_results(
    results: Any,
    output_format: Union[str, ExportFormat] = ExportFormat.JSON,
    output_path: None = None,
    **kwargs: Any
) -> str:
    ...

@overload
def export_results(
    results: Any,
    output_format: Union[str, ExportFormat],
    output_path: Union[str, Path],
    **kwargs: Any
) -> None:
    ...

def export_results(
    results: Any,
    output_format: Union[str, ExportFormat] = ExportFormat.JSON,
    output_path: Optional[Union[str, Path]] = None,
    **kwargs: Any,
) -> Optional[str]:
    """Export simulation results to the specified format.
    
    This function provides a unified interface for exporting simulation results
    to various formats including JSON, YAML, CSV, and HTML. It handles
    serialization of different data types and provides options for customizing
    the output.
    
    Args:
        results: The simulation results to export. Can be any Python object,
            but some formats may have specific requirements (e.g., CSV requires
            a sequence of dictionaries).
        output_format: The format to export to. Can be either a string
            ("json", "yaml", "csv", "html") or an ExportFormat enum.
        output_path: Optional path to save the exported file. If not provided,
            the function returns the exported data as a string. If provided,
            the function writes to the file and returns None.
        **kwargs: Additional format-specific options:
            - For JSON: Additional arguments for json.dumps()
            - For YAML: Additional arguments for yaml.dump()
            - For CSV: Additional arguments for csv.DictWriter
            - For HTML: Additional styling options (see _export_to_html)
    
    Returns:
        If output_path is None, returns the exported data as a string.
        If output_path is provided, returns None after writing to the file.
    
    Raises:
        ExportError: If the export fails for any reason.
        ValueError: If the output format is not supported or the data is not
            compatible with the chosen format.
    
    Examples:
        ```python
        # Export to JSON string
        json_str = export_results({"a": 1, "b": 2})
        
        # Export to YAML file
        export_results({"a": 1, "b": 2}, "yaml", "output.yaml")
        
        # Export list of dicts to CSV
        data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        csv_str = export_results(data, "csv")
        
        # Export to HTML with custom styling
        html = export_results(
            data, 
            "html",
            title="My Results",
            header_style="background-color: #4CAF50; color: white;"
        )
        ```
    """
    # Preprocess results to handle special cases
    if hasattr(results, 'to_dict') and callable(results.to_dict):
        results = results.to_dict()
    elif hasattr(results, '__dict__'):
        # For objects without a to_dict method but with __dict__
        results = results.__dict__
    try:
        # Convert string format to enum if needed
        if isinstance(output_format, str):
            output_format_enum = ExportFormat.from_string(output_format)
        else:
            output_format_enum = output_format
        
        # Export to the specified format
        if output_format_enum == ExportFormat.JSON:
            data = _export_to_json(results, **kwargs)
        elif output_format_enum == ExportFormat.YAML:
            data = _export_to_yaml(results, **kwargs)
        elif output_format_enum == ExportFormat.CSV:
            if not isinstance(results, (list, tuple)) or not results:
                raise ValueError(
                    "CSV export requires a non-empty sequence of dictionaries"
                )
            data = _export_to_csv(results, **kwargs)
        elif output_format_enum == ExportFormat.HTML:
            title = kwargs.pop('title', 'Simulation Results')
            data = _export_to_html(
                results if isinstance(results, (list, tuple)) else [results],
                title=title,
                **kwargs
            )
        else:
            raise ValueError(f"Unsupported export format: {output_format_enum}")
        
        # Write to file if output_path is provided
        if output_path is not None:
            output_path = Path(output_path)
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(data)
                logger.info("Successfully exported results to %s", output_path)
                return None
            except (IOError, OSError) as e:
                raise ExportError(
                    f"Failed to write to {output_path}: {str(e)}"
                ) from e
        
        return data
        
    except Exception as e:
        if not isinstance(e, (ValueError, ExportError)):
            raise ExportError(
                f"Failed to export results: {str(e)}"
            ) from e
        raise
