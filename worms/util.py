"""TODO: Summary
"""
import os
import re
import functools as ft
import itertools as it
import operator
import numpy as np
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures import as_completed as cf_as_completed
import multiprocessing
import threading
from homog import hrot
import pandas as pd
import numba as nb
try:
    # god, I'm so tired of this crap....
    from pyrosetta import rosetta as ros
    HAVE_PYROSETTA = True
except ImportError:
    HAVE_PYROSETTA = False

jit = nb.njit(nogil=True, fastmath=True)


@jit
def expand_array_if_needed(ary, i):
    if len(ary) > i:
        return ary
    newshape = (ary.shape[0] * 2, ) + ary.shape[1:]
    new = np.zeros(newshape, dtype=ary.dtype) - ary.dtype.type(1)
    new[:len(ary)] = ary
    return new


class InProcessExecutor:
    """TODO: Summary
    """

    def __init__(self, *args, **kw):
        """TODO: Summary

        Args:
            *args: Description
            kw: passthru args        """
        pass

    def __enter__(self):
        """TODO: Summary

        Returns:
            TYPE: Description
        """
        return self

    def __exit__(self, *args):
        """TODO: Summary

        Args:
            *args: Description
        """
        pass

    def submit(self, function, *args, **kw):
        """TODO: Summary

        Args:
            function (TYPE): Description
            args: Description
            kw: passthru args
        Returns:
            TYPE: Description
        """
        return NonFuture(function(*args, **kw))

    def map(self, function, *iterables):
        """TODO: Summary

        Args:
            function (TYPE): Description
            iterables: Description

        Returns:
            TYPE: Description
        """
        return map(function, *iterables)
        # return (NonFuture(function(*args) for args in zip(iterables)))


class NonFuture:
    """TODO: Summary
    """

    def __init__(self, result):
        """TODO: Summary

        Args:
            result (TYPE): Description
        """
        self._result = result
        self._condition = threading.Condition()
        self._state = 'FINISHED'
        self._waiters = []

    def result(self):
        """TODO: Summary

        Returns:
            TYPE: Description
        """
        return self._result


def cpu_count():
    """TODO: Summary

    Returns:
        TYPE: Description
    """
    try:
        return int(os.environ['SLURM_CPUS_ON_NODE'])
    except:
        return multiprocessing.cpu_count()


def parallel_batch_map(
        pool, function, accumulator, batch_size, map_func_args, **kw
):
    """TODO: Summary

    Args:
        pool (TYPE): Description
        function (TYPE): Description
        accumulator (TYPE): Description
        batch_size (TYPE): Description
        map_func_args (TYPE): Description
        kw: passthru args
    Yields:
        TYPE: Description
    """
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    os.environ['NUMEXPR_NUM_THREADS'] = '1'
    njobs = len(map_func_args[0])
    args = list(zip(*map_func_args))
    for ibatch in range(0, njobs, batch_size):
        beg = ibatch
        end = min(njobs, ibatch + batch_size)
        batch_args = args[beg:end]  # todo, this could be done lazily...
        futures = [pool.submit(function, *a) for a in batch_args]
        if isinstance(pool, (ProcessPoolExecutor, ThreadPoolExecutor)):
            as_completed = cf_as_completed
        elif isinstance(pool, InProcessExecutor):
            as_completed = lambda x: x
        else:
            from dask.distributed import as_completed as dd_as_completed
            as_completed = dd_as_completed
        for _ in accumulator.accumulate(as_completed(futures)):
            yield None
        accumulator.checkpoint()


def parallel_nobatch_map(
        pool, function, accumulator, batch_size, map_func_args, **kw
):
    """TODO: Summary

    Args:
        pool (TYPE): Description
        function (TYPE): Description
        accumulator (TYPE): Description
        batch_size (TYPE): Description
        map_func_args (TYPE): Description
        kw: passthru args
    Yields:
        TYPE: Description
    """
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    os.environ['NUMEXPR_NUM_THREADS'] = '1'
    njobs = len(map_func_args[0])
    args = list(zip(*map_func_args))
    futures = [pool.submit(function, *a) for a in args]
    if isinstance(pool, (ProcessPoolExecutor, ThreadPoolExecutor)):
        as_completed = cf_as_completed
    else:
        as_completed = dd_as_completed
    for _ in accumulator.accumulate(as_completed(futures)):
        yield None
    accumulator.checkpoint()


def tqdm_parallel_map(
        pool, function, accumulator, map_func_args, batch_size, **kw
):
    """TODO: Summary

    Args:
        pool (TYPE): Description
        function (TYPE): Description
        accumulator (TYPE): Description
        map_func_args (TYPE): Description
        batch_size (TYPE): Description
        kw: passthru args    """
    for _ in tqdm(parallel_batch_map(pool, function, accumulator, batch_size,
                                     map_func_args=map_func_args, **kw),
                  total=len(map_func_args[0]), **kw):
        pass


def numpy_stub_from_rosetta_stub(rosstub):
    """TODO: Summary

    Args:
        rosstub (TYPE): Description

    Returns:
        TYPE: Description
    """
    npstub = np.zeros((4, 4))
    for i in range(3):
        npstub[..., i, 3] = rosstub.v[i]
        for j in range(3):
            npstub[..., i, j] = rosstub.M(i + 1, j + 1)
    npstub[..., 3, 3] = 1.0
    return npstub


def rosetta_stub_from_numpy_stub(npstub):
    """TODO: Summary

    Args:
        npstub (TYPE): Description

    Returns:
        TYPE: Description
    """
    rosstub = ros.core.kinematics.Stub()
    rosstub.M.xx = npstub[0, 0]
    rosstub.M.xy = npstub[0, 1]
    rosstub.M.xz = npstub[0, 2]
    rosstub.M.yx = npstub[1, 0]
    rosstub.M.yy = npstub[1, 1]
    rosstub.M.yz = npstub[1, 2]
    rosstub.M.zx = npstub[2, 0]
    rosstub.M.zy = npstub[2, 1]
    rosstub.M.zz = npstub[2, 2]
    rosstub.v.x = npstub[0, 3]
    rosstub.v.y = npstub[1, 3]
    rosstub.v.z = npstub[2, 3]
    return rosstub


def get_bb_stubs(pose, which_resi=None):
    """extract rif style stubs from rosetta pose

    Args:
        pose (TYPE): Description
        which_resi (None, optional): Description

    Returns:
        TYPE: Description

    Raises:
        ValueError: Description
    """
    if which_resi is None:
        which_resi = list(range(1, pose.size() + 1))
    npstubs, n_ca_c = [], []
    for ir in which_resi:
        r = pose.residue(ir)
        if not r.is_protein():
            raise ValueError(
                'non-protein residue %s at position %i' % (r.name(), ir)
            )
        n, ca, c = r.xyz('N'), r.xyz('CA'), r.xyz('C')
        ros_stub = ros.core.kinematics.Stub(ca, n, ca, c)
        npstubs.append(numpy_stub_from_rosetta_stub(ros_stub))
        n_ca_c.append(
            np.array([[n.x, n.y, n.z],
                      [ca.x, ca.y, ca.z],
                      [c.x, c.y, c.z]])
        )
    return np.stack(npstubs).astype('f8'), np.stack(n_ca_c).astype('f8')


def get_bb_coords(pose, which_resi=None):
    """extract rif style stubs from rosetta pose

    Args:
        pose (TYPE): Description
        which_resi (None, optional): Description

    Returns:
        TYPE: Description

    Raises:
        ValueError: Description
    """
    if which_resi is None:
        which_resi = list(range(1, pose.size() + 1))
    n_ca_c = []
    for ir in which_resi:
        r = pose.residue(ir)
        if not r.is_protein():
            raise ValueError(
                'non-protein residue %s at position %i' % (r.name(), ir)
            )
        n, ca, c = r.xyz('N'), r.xyz('CA'), r.xyz('C')
        n_ca_c.append(
            np.array([
                [n.x, n.y, n.z, 1],
                [ca.x, ca.y, ca.z, 1],
                [c.x, c.y, c.z, 1],
            ])
        )
    return np.stack(n_ca_c).astype('f8')


def get_chain_bounds(pose):
    """TODO: Summary

    Args:
        pose (TYPE): Description

    Returns:
        TYPE: Description
    """
    ch = np.array([pose.chain(i + 1) for i in range(len(pose))])
    chains = list()
    for i in range(ch[-1]):
        chains.append((np.sum(ch <= i), np.sum(ch <= i + 1)))
    assert chains[0][0] == 0
    assert chains[-1][-1] == len(pose)
    return chains


def pose_bounds(pose, lb, ub):
    """TODO: Summary

    Args:
        pose (TYPE): Description
        lb (TYPE): Description
        ub (TYPE): Description

    Returns:
        TYPE: Description

    Raises:
        ValueError: Description
    """
    if ub < 0: ub = len(pose) + 1 + ub
    if lb < 1 or ub > len(pose):
        raise ValueError(
            'lb/ub ' + str(lb) + '/' + str(ub) +
            ' out of bounds for pose with len ' + str(len(pose))
        )
    return lb, ub


def subpose(pose, lb, ub=-1):
    """TODO: Summary

    Args:
        pose (TYPE): Description
        lb (TYPE): Description
        ub (TYPE, optional): Description

    Returns:
        TYPE: Description
    """
    lb, ub = pose_bounds(pose, lb, ub)
    p = ros.core.pose.Pose()
    ros.core.pose.append_subpose_to_pose(p, pose, lb, ub)
    return p


def xform_pose(xform, pose, lb=1, ub=-1):
    """TODO: Summary

    Args:
        xform (TYPE): Description
        pose (TYPE): Description
        lb (int, optional): Description
        ub (TYPE, optional): Description
    """
    lb, ub = pose_bounds(pose, lb, ub)
    xform = rosetta_stub_from_numpy_stub(xform.reshape(4, 4))
    ros.protocols.sic_dock.xform_pose(pose, xform, lb, ub)


def worst_CN_connect(p):
    """TODO: Summary

    Args:
        p (TYPE): Description

    Returns:
        TYPE: Description
    """
    for ir in range(1, len(p)):
        worst = 0
        if (p.residue(ir).is_protein() and p.residue(ir + 1).is_protein()
                and not (ros.core.pose.is_upper_terminus(p, ir)
                         or ros.core.pose.is_lower_terminus(p, ir + 1))):
            dist = p.residue(ir).xyz('C').distance(p.residue(ir + 1).xyz('N'))
            worst = max(abs(dist - 1.32), worst)
    return worst


def no_overlapping_adjacent_residues(p):
    """TODO: Summary

    Args:
        p (TYPE): Description

    Returns:
        TYPE: Description
    """
    for ir in range(1, len(p)):
        if (p.residue(ir).is_protein() and p.residue(ir + 1).is_protein()):
            dist = p.residue(ir).xyz('CA').distance(
                p.residue(ir + 1).xyz('CA')
            )
            if dist < 0.1: return False
    return True


def no_overlapping_residues(p):
    """TODO: Summary

    Args:
        p (TYPE): Description

    Returns:
        TYPE: Description
    """
    for ir in range(1, len(p) + 1):
        if not p.residue(ir).is_protein():
            continue
        for jr in range(1, ir):
            if not p.residue(jr).is_protein():
                continue
            dist = p.residue(ir).xyz('CA').distance(p.residue(jr).xyz('CA'))
            if dist < 0.5: return False
    return True


def trim_pose(pose, resid, direction, pad=0):
    """trim end of pose from direction, leaving <=pad residues beyond resid

    Args:
        pose (TYPE): Description
        resid (TYPE): Description
        direction (TYPE): Description
        pad (int, optional): Description

    Returns:
        TYPE: Description

    Raises:
        ValueError: Description
    """
    if direction not in "NC":
        raise ValueError("direction must be 'N' or 'C'")
    if not 0 < resid <= len(pose):
        raise ValueError("resid %i out of bounds %i" % (resid, len(pose)))
    p = ros.core.pose.Pose()
    if direction == 'N':
        lb, ub = max(resid - pad, 1), len(pose)
    elif direction == 'C':
        lb, ub = 1, min(resid + pad, len(pose))
    # print('_trim_pose lbub', lb, ub, 'len', len(pose), 'resid', resid)
    ros.core.pose.append_subpose_to_pose(p, pose, lb, ub)
    return p, lb, ub


def symfile_path(name):
    """TODO: Summary

    Args:
        name (TYPE): Description

    Returns:
        TYPE: Description
    """
    path, _ = os.path.split(__file__)
    return os.path.join(path, 'rosetta_symdef', name + '.sym')


@ft.lru_cache()
def get_symfile_contents(name):
    """TODO: Summary

    Args:
        name (TYPE): Description

    Returns:
        TYPE: Description
    """
    with open(symfile_path(name)) as f:
        return f.read()


@ft.lru_cache()
def get_symdata(name):
    """TODO: Summary

    Args:
        name (TYPE): Description

    Returns:
        TYPE: Description
    """
    if name is None: return None
    ss = ros.std.stringstream(get_symfile_contents(name))
    d = ros.core.conformation.symmetry.SymmData()
    d.read_symmetry_data_from_stream(ss)
    return d


def get_symdata_modified(
        name, string_substitutions=None, scale_positions=None
):
    """TODO: Summary

    Args:
        name (TYPE): Description
        string_substitutions (None, optional): Description
        scale_positions (None, optional): Description

    Returns:
        TYPE: Description
    """
    if name is None: return None
    symfilestr = get_symfile_contents(name)
    if scale_positions is not None:
        if string_substitutions is None:
            string_substitutions = dict()
        for line in symfilestr.splitlines():
            if not line.startswith('xyz'): continue
            posstr = re.split('\s+', line)[-1]
            x, y, z = [float(x) * scale_positions for x in posstr.split(',')]
            string_substitutions[posstr] = '%f,%f,%f' % (x, y, z)
    if string_substitutions is not None:
        for k, v in string_substitutions.items():
            symfilestr = symfilestr.replace(k, v)
    ss = ros.std.stringstream(symfilestr)
    d = ros.core.conformation.symmetry.SymmData()
    d.read_symmetry_data_from_stream(ss)
    return d


def infer_cyclic_symmetry(pose):
    """TODO: Summary

    Args:
        pose (TYPE): Description

    Raises:
        NotImplementedError: Description
    """
    raise NotImplementedError


def bigprod(iterable):
    """TODO: Summary

    Args:
        iterable (TYPE): Description

    Returns:
        TYPE: Description
    """
    return ft.reduce(operator.mul, iterable, 1)


class MultiRange:
    """TODO: Summary

    Attributes:
        len (TYPE): Description
        nside (TYPE): Description
        psum (TYPE): Description
    """

    def __init__(self, nside):
        """TODO: Summary

        Args:
            nside (TYPE): Description
        """
        self.nside = np.array(nside, dtype='i')
        self.psum = np.concatenate([
            np.cumprod(self.nside[1:][::-1])[::-1], [1]
        ])
        assert np.all(self.psum > 0)
        assert bigprod(self.nside[1:]) < 2**63
        self.len = bigprod(self.nside)

    def __getitem__(self, idx):
        """TODO: Summary

        Args:
            idx (TYPE): Description

        Returns:
            TYPE: Description

        Raises:
            StopIteration: Description
        """
        if isinstance(idx, slice):
            return (self[i] for i in range(self.len)[idx])
        if idx >= self.len:
            raise StopIteration
        return tuple((idx // self.psum) % self.nside)

    def __len__(self):
        """TODO: Summary

        Returns:
            TYPE: Description
        """
        return self.len


def first_duplicate(segs):
    """TODO: Summary

    Args:
        segs (TYPE): Description

    Returns:
        TYPE: Description
    """
    for i in range(len(segs) - 1, 0, -1):
        for j in range(i):
            if segs[i].spliceables == segs[j].spliceables:
                return j
    return None


def dicts_to_items(inp):
    """TODO: Summary

    Args:
        inp (TYPE): Description

    Returns:
        TYPE: Description
    """
    if isinstance(inp, list):
        return [dicts_to_items(x) for x in inp]
    elif isinstance(inp, dict):
        return [(k, dicts_to_items(v)) for k, v in inp.items()]
    return inp


def items_to_dicts(inp):
    """TODO: Summary

    Args:
        inp (TYPE): Description

    Returns:
        TYPE: Description
    """
    if (isinstance(inp, list) and isinstance(inp[0], tuple)
            and len(inp[0]) is 2):
        return {k: items_to_dicts(v) for k, v in inp}
    elif isinstance(inp, list):
        return [items_to_dicts(x) for x in inp]
    return inp


def residue_coords(p, ir, n=3):
    crd = (p.residue(ir).xyz(i) for i in range(1, n + 1))
    return np.stack([c.x, c.y, c.z, 1] for c in crd)


def residue_sym_err(p, ang, ir, jr, n=1, axis=[0, 0, 1], verbose=0):
    mxdist = 0
    for i in range(n):
        xyz0 = residue_coords(p, ir + i)
        xyz1 = residue_coords(p, jr + i)
        xyz3 = hrot(axis, ang) @ xyz1.T
        xyz4 = hrot(axis, -ang) @ xyz1.T
        if verbose:
            print(i, xyz0)
            print(i, xyz1)
            print(i, xyz3.T)
            print(i, xyz4.T)
            print()
        mxdist = max(
            mxdist,
            min(
                np.max(np.sum((xyz0 - xyz3.T)**2, axis=1)),
                np.max(np.sum((xyz0 - xyz4.T)**2, axis=1))
            )
        )
    return np.sqrt(mxdist)


def unique_key(a, b=None):
    """Summary

    Args:
        a (TYPE): Description
        b (TYPE): Description

    Returns:
        TYPE: Description
    """
    if b is None:
        raise NotImplementedError
    mi = pd.MultiIndex.from_arrays([a, b]).drop_duplicates()
    return mi.get_indexer([a, b])


@nb.njit('int32[:](int32[:])', nogil=1)
def contig_idx_breaks(idx):
    breaks = np.empty(idx[-1] + 2, dtype=np.int32)
    breaks[0] = 0
    n = 1
    for i in range(1, len(idx)):
        if idx[i - 1] != idx[i]:
            assert idx[i - 1] < idx[i]
            breaks[n] = i
            n += 1
    breaks[n] = len(idx)
    breaks = np.ascontiguousarray(breaks[:n + 1])
    if __debug__:
        for i in range(breaks.size - 1):
            vals = idx[breaks[i]:breaks[i + 1]]
            assert len(vals)
            assert np.all(vals == vals[0])
    return breaks
