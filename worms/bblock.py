import numpy as np
import numba as nb
from worms import util
from worms.util import jit
import numba.types as nt
import homog


def ncac_to_stubs(ncac):
    """
        Vector const & center,
        Vector const & a,
        Vector const & b,
        Vector const & c
    )
    {
        Vector e1( a - b);
        e1.normalize();

        Vector e3( cross( e1, c - b ) );
        e3.normalize();

        Vector e2( cross( e3,e1) );
        M.col_x( e1 ).col_y( e2 ).col_z( e3 );
        v = center;
    """
    assert ncac.shape[1:] == (3, 4)
    stubs = np.zeros((len(ncac), 4, 4), dtype=np.float64)
    ca2n = (ncac[:, 0] - ncac[:, 1])[..., :3]
    ca2c = (ncac[:, 2] - ncac[:, 1])[..., :3]
    # tgt1 = ca2n + ca2c  # thought this might make
    # tgt2 = ca2n - ca2c  # n/c coords match better
    tgt1 = ca2n  # rosetta style
    tgt2 = ca2c  # seems better
    a = tgt1
    a /= np.linalg.norm(a, axis=-1)[:, None]
    c = np.cross(a, tgt2)
    c /= np.linalg.norm(c, axis=-1)[:, None]
    b = np.cross(c, a)
    assert np.allclose(np.sum(a * b, axis=-1), 0)
    assert np.allclose(np.sum(b * c, axis=-1), 0)
    assert np.allclose(np.sum(c * a, axis=-1), 0)
    assert np.allclose(np.linalg.norm(a, axis=-1), 1)
    assert np.allclose(np.linalg.norm(b, axis=-1), 1)
    assert np.allclose(np.linalg.norm(c, axis=-1), 1)
    stubs[:, :3, 0] = a
    stubs[:, :3, 1] = b
    stubs[:, :3, 2] = c
    stubs[:, :3, 3] = ncac[:, 1, :3]
    stubs[:, 3, 3] = 1
    return stubs


def BBlock(entry, pdbfile, pose, ss):
    chains = util.get_chain_bounds(pose)
    ss = np.frombuffer(ss.encode(), dtype='i1')

    ncac = util.get_bb_coords(pose)
    stubs = ncac_to_stubs(ncac)

    assert len(pose) == len(ncac)
    assert len(pose) == len(stubs)
    assert len(pose) == len(ss)
    conn = _make_connections_array(entry['connections'], chains)
    if len(conn) is 0:
        print('bad conn info!', pdbfile)
        return None, pdbfile  # new, missing
    if ncac.shape[-1] is 4:
        ncac = ncac.astype(np.float64)
    elif ncac.shape[-1] is 3:
        tmp = np.ones((ncac.shape[0], 3, 4), dtype=np.float64)
        tmp[..., :3] = ncac
        ncac = tmp
    else:
        assert 0, 'bad ncac'

    # print(ncac.shape, ncac.strides)
    bblock = _BBlock(
        connections=conn,
        file=np.frombuffer(entry['file'].encode(), dtype='i1'),
        components=np.frombuffer(
            str(entry['components']).encode(), dtype='i1'),
        protocol=np.frombuffer(entry['protocol'].encode(), dtype='i1'),
        name=np.frombuffer(entry['name'].encode(), dtype='i1'),
        classes=np.frombuffer(','.join(entry['class']).encode(), 'i1'),
        validated=entry['validated'],
        _type=np.frombuffer(entry['type'].encode(), dtype='i1'),
        base=np.frombuffer(entry['base'].encode(), dtype='i1'),
        ncac=np.ascontiguousarray(ncac),
        chains=np.array(chains, dtype='i4'),
        ss=ss,
        stubs=np.ascontiguousarray(stubs.astype('f8')),
    )

    return bblock


@nb.jitclass((
    ('connections', nt.int32[:, :]),
    ('file'       , nt.int8[:]),
    ('components' , nt.int8[:]),
    ('protocol'   , nt.int8[:]),
    ('name'       , nt.int8[:]),
    ('classes'    , nt.int8[:]),
    ('validated'  , nt.boolean),
    ('_type'      , nt.int8[:]),
    ('base'       , nt.int8[:]),
    ('ncac'       , nt.float64[:, :, :]),
    ('chains'     , nt.int32[:,:]),
    ('ss'         , nt.int8[:]),
    ('stubs'      , nt.float64[:, :, :]),
))  # yapf: disable
class _BBlock:
    """
    contains data for a single structure building block

    Attributes:
        base (TYPE): Description
        chains (TYPE): Description
        classes (TYPE): Description
        components (TYPE): Description
        connections (TYPE): Description
        file (TYPE): Description
        name (TYPE): Description
        ncac (TYPE): Description
        protocol (TYPE): Description
        ss (TYPE): Description
        stubs (TYPE): Description
        validated (TYPE): Description
    """

    def __init__(self, connections, file, components, protocol, name, classes,
                 validated, _type, base, ncac, chains, ss, stubs):
        """TODO: Summary

        Args:
            connections (TYPE): Description
            file (TYPE): Description
            components (TYPE): Description
            protocol (TYPE): Description
            name (TYPE): Description
            classes (TYPE): Description
            validated (TYPE): Description
            _type (TYPE): Description
            base (TYPE): Description
            ncac (TYPE): Description
            chains (TYPE): Description
            ss (TYPE): Description
            stubs (TYPE): Description
        """
        self.connections = connections
        self.file = file
        self.components = components
        self.protocol = protocol
        self.name = name
        self.classes = classes
        self.validated = validated
        self._type = _type
        self.base = base
        self.ncac = ncac
        self.chains = chains
        self.ss = ss
        self.stubs = stubs
        assert np.isnan(np.sum(self.ncac)) == False
        assert np.isnan(np.sum(self.stubs)) == False
        assert np.isnan(np.sum(self.ss)) == False
        assert np.isnan(np.sum(self.chains)) == False

    @property
    def n_connections(self):
        """Summary

        Returns:
            TYPE: Description
        """
        return len(self.connections)

    def conn_dirn(self, i):
        """Summary

        Args:
            i (TYPE): Description

        Returns:
            TYPE: Description
        """
        return self.connections[i, 0]

    def conn_resids(self, i):
        """Summary

        Args:
            i (TYPE): Description

        Returns:
            TYPE: Description
        """
        return self.connections[i, 2:self.connections[i, 1]]

    @property
    def _state(self):
        """TODO: Summary

        Returns:
            TYPE: Description
        """
        return (self.connections, self.file, self.components, self.protocol,
                self.name, self.classes, self.validated, self._type, self.base,
                self.ncac, self.chains, self.ss, self.stubs)


@jit
def chain_of_ires(bb, ires):
    """Summary

    Args:
        bb (TYPE): Description
        ires (TYPE): Description

    Returns:
        TYPE: Description
    """
    chain = np.empty_like(ires)
    for i, ir in enumerate(ires):
        if ir < 0:
            chain[i] = -1
        else:
            for c in range(len(bb.chains)):
                if bb.chains[c, 0] <= ir < bb.chains[c, 1]:
                    chain[i] = c
    return chain


def _make_connections_array(entries, chain_bounds):
    """TODO: Summary

    Args:
        entries (TYPE): Description
        chain_bounds (TYPE): Description

    Returns:
        TYPE: Description
    """
    try:
        reslists = [_get_connection_residues(e, chain_bounds) for e in entries]
    except Exception as e:
        print('WARNING: make_connections_array failed on', entries,
              'error was:', e)
        return np.zeros((0, 0))
    mx = max(len(x) for x in reslists)
    conn = np.zeros((len(reslists), mx + 2), 'i4') - 1
    for i, ires_ary in enumerate(reslists):
        conn[i, 0] = entries[i]['direction'] == 'C'
        conn[i, 1] = len(ires_ary) + 2
        conn[i, 2:conn[i, 1]] = ires_ary
    return conn
    # print(chain_bounds)
    # print(repr(conn))


def _get_connection_residues(entry, chain_bounds):
    """TODO: Summary

    Args:
        entry (TYPE): Description
        chain_bounds (TYPE): Description

    Returns:
        TYPE: Description
    """
    chain_bounds[-1][-1]
    r, c, d = entry['residues'], int(entry['chain']), entry['direction']
    if isinstance(r, str) and r.startswith('['):
        r = eval(r)
    if isinstance(r, list):
        try:
            return [int(_) for _ in r]
        except ValueError:
            assert len(r) is 1
            r = r[0]
    if r.count(','):
        c2, r = r.split(',')
        assert int(c2) == c
    b, e = r.split(':')
    if b == '-': b = 0
    if e == '-': e = -1
    nres = chain_bounds[c - 1][1] - chain_bounds[c - 1][0]
    b = int(b) if b else 0
    e = int(e) if e else nres
    if e < 0: e += nres
    return np.array(range(*chain_bounds[c - 1])[b:e], dtype='i4')


def bblock_components(bblock):
    """TODO: Summary

    Args:
        bblock (TYPE): Description

    Returns:
        TYPE: Description
    """
    return eval(bytes(bblock.components))


def bblock_str(bblock):
    """TODO: Summary

    Args:
        bblock (TYPE): Description

    Returns:
        TYPE: Description
    """
    return '\n'.join([
        'jitclass BBlock(',
        '    file=' + str(bytes(bblock.file)),
        '    components=' + str(bblock_components(bblock)),
        '    protocol=' + str(bytes(bblock.protocol)),
        '    name=' + str(bytes(bblock.name)),
        '    classes=' + str(bytes(bblock.classes)),
        '    validated=' + str(bblock.validated),
        '    _type=' + str(bytes(bblock._type)),
        '    base=' + str(bytes(bblock.base)),
        '    ncac=array(shape=' + str(bblock.ncac.shape) + ', dtype=' +
        str(bblock.ncac.dtype) + ')',
        '    chains=' + str(bblock.chains),
        '    ss=array(shape=' + str(bblock.ss.shape) + ', dtype=' +
        str(bblock.ss.dtype) + ')',
        '    stubs=array(shape=' + str(bblock.stubs.shape) + ', dtype=' + str(
            bblock.connections.dtype) + ')',
        '    connectionsZ=array(shape=' + str(bblock.connections.shape) +
        ', dtype=' + str(bblock.connections.dtype) + ')',
        ')',
    ])
