"""Boundary for optional reference-acquisition modes retained by old validators."""
def unavailable(*args, **kwargs):
    raise ValueError('Native trace acquisition is available in the Research repository')

identity = unavailable
unchanged = unavailable
trace_bundle = unavailable
