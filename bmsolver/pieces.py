"""Belt pieces and how they connect, as the game does it.

A tile holds at most one piece. A piece is written as a two-character code: its kind and its direction.

    kind  B  belt              takes items from its back and both sides, pushes them out of its front
          S  splitter          takes items from its back only, pushes them out of front, left and right,
                               sharing equally between the sides that accept
          I  underground in    takes items from its back only, sends them to the first underground out
                               of the same direction 1..7 tiles ahead
          O  underground out   takes nothing from its neighbours, pushes out of its front

    direction 0 north (y - 1), 1 west (x - 1), 2 south (y + 1), 3 east (x + 1)

So 'B1' is a belt moving west and 'S3' a splitter facing east. Coordinates are (x, y) with y growing south,
the same as in game blueprints.
"""

DIRS = {0: (0, -1), 1: (-1, 0), 2: (0, 1), 3: (1, 0)}
NORTH, WEST, SOUTH, EAST = 0, 1, 2, 3
SIDE_NAMES = {'N': NORTH, 'W': WEST, 'S': SOUTH, 'E': EAST}
KINDS = 'BSIO'
UG_REACH = 7          # an underground exit can be 1..7 tiles ahead of its entrance (6 tiles between)

# game blueprint building ids
BUILDING_ID = {'B': 3000, 'S': 3001, 'I': 3002, 'O': 3003}
KIND_OF_ID = {v: k for k, v in BUILDING_ID.items()}


def opposite(d):
    return (d + 2) % 4


def step(p, d, n=1):
    """The tile n steps from p in direction d."""
    return (p[0] + n * DIRS[d][0], p[1] + n * DIRS[d][1])


def push_dirs(code):
    """Directions a piece pushes items into."""
    k, o = code[0], int(code[1])
    if k in 'BO':
        return [o]
    if k == 'S':
        return [o, (o + 1) % 4, (o + 3) % 4]
    return []


def accepts(code, d):
    """Does a piece accept items moving in direction d (arriving from the tile on its opposite side)?"""
    k, o = code[0], int(code[1])
    if k == 'B':
        return o != opposite(d)
    if k in 'SI':
        return o == d
    return False


def direction_between(p, q):
    """Direction of the single step from p to the neighbouring tile q, or None."""
    for d, (dx, dy) in DIRS.items():
        if (p[0] + dx, p[1] + dy) == q:
            return d
    return None


# mirror images of directions
MIRROR_Y = {0: 2, 1: 1, 2: 0, 3: 3}     # flip top <-> bottom
MIRROR_X = {0: 0, 1: 3, 2: 2, 3: 1}     # flip left <-> right


def mirror_code(code, axis):
    """The same piece seen in a mirror. axis 'y' flips top/bottom, 'x' flips left/right."""
    table = MIRROR_Y if axis == 'y' else MIRROR_X
    return code[0] + str(table[int(code[1])])
