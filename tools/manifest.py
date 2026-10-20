"""Read the flat string fields in our ConfigObj-compatible add-on manifests.

Using Python's literal parser here handles quoted multiline descriptions without
adding a build dependency. This reader does not execute the manifest.
"""
import ast


def readManifest(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    fields = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0], ast.Name):
            raise ValueError('Unsupported manifest entry')
        name = node.targets[0].id
        value = ast.literal_eval(node.value)
        if name in fields or not isinstance(value,str):
            raise ValueError('Invalid manifest field: '+name)
        fields[name] = value
    return fields
