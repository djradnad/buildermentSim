"""Using bmsolver from Python instead of the command line."""
from bmsolver import spec, search
from bmsolver.design import Design

# smallest 1:7 splitter, mirror symmetric except the middle row
result = search.find(spec.splitter(7), symmetry='y', quasi=True, timeout=60, reporter=search.PrintReporter())
print(result['W'], result['H'], 'proven' if result['proven'] else 'not proven')
result['design'].save('splitter_1_7.json', name='1:7 quasi')     # blueprint json (community format)

# your own shape: 3 inputs on the east side at fixed rows, 2 outputs at the bottom
custom = spec.Spec([spec.Group('E', 3, at=1)], [spec.Group('S', 2)], mix=True)
print(search.find(custom, timeout=60)['design'].to_text())
