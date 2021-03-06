import pytest
import _pickle as pickle
import numpy as np
from homog import hrot, htrans, axis_angle_of, axis_ang_cen_of
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from worms import *
from homog.sym import icosahedral_axes as IA
import time
from worms.tests import only_if_pyrosetta, only_if_pyrosetta_distributed


@only_if_pyrosetta
def test_Segment_merge_split_idx(c1pose):
    helix = Spliceable(c1pose, sites=[((1, 2, 3), 'N'), ((9, 10, 11), 'C')])
    helix2 = Spliceable(c1pose, sites=[((2, 5), 'N'), ((8, 11, 13), 'C')])
    seg = Segment([helix, helix2], 'NC')
    head, tail = seg.make_head(), seg.make_tail()
    head_idx = np.array(
        [i for i in range(len(head)) for j in range(len(tail))])
    tail_idx = np.array(
        [j for i in range(len(head)) for j in range(len(tail))])
    idx = seg.merge_idx_slow(head, head_idx, tail, tail_idx)
    # print('merged_idx', idx)
    head_idx2, tail_idx2 = seg.split_idx(idx, head, tail)
    assert np.all(head_idx2[idx >= 0] == head_idx[idx >= 0])
    assert np.all(tail_idx2[idx >= 0] == tail_idx[idx >= 0])


@only_if_pyrosetta
def test_Segment_split_merge_idx(c1pose):
    helix = Spliceable(c1pose, sites=[((1, 2, 3), 'N'), ((9, 10, 11), 'C')])
    helix2 = Spliceable(c1pose, sites=[((2, 5), 'N'), ((8, 11, 13), 'C')])
    seg = Segment([helix2], 'NC')
    idx = np.arange(len(seg))
    head, tail = seg.make_head(), seg.make_tail()
    head_idx, tail_idx = seg.split_idx(idx, head, tail)
    idx2 = seg.merge_idx_slow(head, head_idx, tail, tail_idx)
    assert np.all(idx == idx2)


@only_if_pyrosetta
def test_Segment_split_merge_invalid_pairs(c1pose):
    helix = Spliceable(
        c1pose, sites=[((1, 2, 3), 'N'), ((9, 10, 11), 'C')], min_seg_len=10)
    helix2 = Spliceable(
        c1pose, sites=[((2, 5), 'N'), ((8, 11, 13), 'C')], min_seg_len=10)
    seg = Segment([helix, helix2], 'NC')
    head, tail = seg.make_head(), seg.make_tail()
    head_idx = np.array(
        [i for i in range(len(head)) for j in range(len(tail))])
    tail_idx = np.array(
        [j for i in range(len(head)) for j in range(len(tail))])
    idx = seg.merge_idx_slow(head, head_idx, tail, tail_idx)
    # print('merged_idx', idx)
    head_idx2, tail_idx2 = seg.split_idx(idx, head, tail)
    assert np.all(head_idx2[idx >= 0] == head_idx[idx >= 0])
    assert np.all(tail_idx2[idx >= 0] == tail_idx[idx >= 0])


@only_if_pyrosetta
def test_SpliceSite(pose, c3pose):
    assert len(pose) == 7
    ss = SpliceSite(1, 'N')
    spliceable = Spliceable(pose, [])
    spliceablec3 = Spliceable(c3pose, [])
    assert 1 == ss.resid(1, spliceable.body)
    assert pose.size() == ss.resid(-1, spliceable.body)
    assert ss._resids(spliceable) == [1]
    assert SpliceSite('1:7', 'N')._resids(spliceable) == [1, 2, 3, 4, 5, 6, 7]
    assert SpliceSite(':7', 'N')._resids(spliceable) == [1, 2, 3, 4, 5, 6, 7]
    assert SpliceSite('-3:-1', 'N')._resids(spliceable) == [5, 6, 7]
    assert SpliceSite('-3:', 'N')._resids(spliceable) == [5, 6, 7]
    assert SpliceSite(':2', 'N')._resids(spliceable) == [1, 2]
    assert SpliceSite(':-5', 'N')._resids(spliceable) == [1, 2, 3]
    assert SpliceSite('::2', 'N')._resids(spliceable) == [1, 3, 5, 7]
    with pytest.raises(ValueError):
        SpliceSite('-1:-3', 'N')._resids(spliceable)
    with pytest.raises(ValueError):
        SpliceSite('-1:3', 'N')._resids(spliceable)
    assert SpliceSite([1, 2, 3], 'N', 1)._resids(spliceablec3) == [1, 2, 3]
    assert SpliceSite([1, 2, 3], 'N', 2)._resids(spliceablec3) == [10, 11, 12]
    assert SpliceSite([1, 2, 3], 'N', 3)._resids(spliceablec3) == [19, 20, 21]


@only_if_pyrosetta
def test_spliceable(c2pose):
    site1 = SpliceSite([1, 2, 3], 'N', 1)
    site2 = SpliceSite([1, 2, 3], 'N', 2)
    dimer = Spliceable(c2pose, sites=[site1, site2])
    assert dimer.sites[0]._resids(dimer) == [1, 2, 3]
    assert dimer.sites[1]._resids(dimer) == [13, 14, 15]

    site1 = {'sele': [1, 2, 3], 'polarity': 'N', 'chain': 1}
    site2 = {'sele': [1, 2, 3], 'polarity': 'N', 'chain': 2}
    dimer = Spliceable(c2pose, sites=[site1, site2])
    assert dimer.sites[0]._resids(dimer) == [1, 2, 3]
    assert dimer.sites[1]._resids(dimer) == [13, 14, 15]

    site1 = ([1, 2, 3], 'N', 1)
    site2 = ([1, 2, 3], 'N', 2)
    dimer = Spliceable(c2pose, sites=[site1, site2])
    assert dimer.sites[0]._resids(dimer) == [1, 2, 3]
    assert dimer.sites[1]._resids(dimer) == [13, 14, 15]

    site1 = (':3', 'N')
    site2 = ('2,:3', 'N')
    dimer = Spliceable(c2pose, sites=[site1, site2])
    assert dimer.sites[0]._resids(dimer) == [1, 2, 3]
    assert dimer.sites[1]._resids(dimer) == [13, 14, 15]


@only_if_pyrosetta_distributed
def test_spliceable_pickle(tmpdir, c2pose):
    site1 = SpliceSite([1, 2, 3], 'N', 1)
    site2 = SpliceSite([1, 2, 3], 'N', 2)
    dimer = Spliceable(c2pose, sites=[site1, site2])
    pickle.dump(dimer, open(os.path.join(str(tmpdir), 'test.pickle'), 'wb'))
    dimer2 = pickle.load(open(os.path.join(str(tmpdir), 'test.pickle'), 'rb'))
    assert str(dimer) == str(dimer2)


@only_if_pyrosetta
def test_segment_geom(c1pose):
    "currently only a basic sanity checkb... only checks translation distances"
    body = c1pose
    stubs, _ = util.get_bb_stubs(body)
    assert stubs.shape == (body.size(), 4, 4)

    nsplice = SpliceSite(
        polarity='N', sele=[
            1,
            2,
        ])
    csplice = SpliceSite(polarity='C', sele=[9, 10, 11, 12, 13])
    Npairs0 = len(nsplice.selections) * len(csplice.selections)

    # N to N and C to C invalid, can't splice to same
    spliceable = Spliceable(body, sites=[nsplice, csplice])
    with pytest.raises(ValueError):
        seg = Segment([spliceable], entry='N', exit='N')
    with pytest.raises(ValueError):
        seg = Segment([spliceable] * 3, entry='C', exit='C')

    # add some extra splice sites
    Nexsite = 2
    spliceable = Spliceable(body, sites=[nsplice, csplice] * Nexsite)

    # test beginning segment.. only has exit
    seg = Segment([spliceable], exit='C')
    assert seg.x2exit.shape == (Nexsite * len(csplice.selections), 4, 4)
    assert seg.x2orgn.shape == (Nexsite * len(csplice.selections), 4, 4)
    assert np.all(seg.x2exit[..., 3, :3] == 0)
    assert np.all(seg.x2exit[..., 3, 3] == 1)
    for e2x, e2o, ir, jr in zip(seg.x2exit, seg.x2orgn, seg.entryresid,
                                seg.exitresid):
        assert ir == -1
        assert np.allclose(e2o, np.eye(4))
        assert np.allclose(e2x, stubs[jr - 1])

    # test middle segment with entry and exit
    seg = Segment([spliceable], 'N', 'C')
    assert seg.x2exit.shape == (Nexsite**2 * Npairs0, 4, 4)
    assert seg.x2orgn.shape == (Nexsite**2 * Npairs0, 4, 4)
    assert np.all(seg.x2exit[..., 3, :3] == 0)
    assert np.all(seg.x2exit[..., 3, 3] == 1)
    for e2x, e2o, ir, jr in zip(seg.x2exit, seg.x2orgn, seg.entryresid,
                                seg.exitresid):
        assert np.allclose(stubs[ir - 1] @ e2o, np.eye(4), atol=1e-5)
        assert np.allclose(stubs[ir - 1] @ e2x, stubs[jr - 1], atol=1e-5)

    # test ending segment.. only has entry
    seg = Segment([spliceable], entry='N')
    assert seg.x2exit.shape == (Nexsite * len(nsplice.selections), 4, 4)
    assert seg.x2orgn.shape == (Nexsite * len(nsplice.selections), 4, 4)
    assert np.all(seg.x2exit[..., 3, :3] == 0)
    assert np.all(seg.x2exit[..., 3, 3] == 1)
    for e2x, e2o, ir, jr in zip(seg.x2exit, seg.x2orgn, seg.entryresid,
                                seg.exitresid):
        assert jr == -1
        assert np.allclose(e2o, e2x)
        assert np.allclose(e2o @ stubs[ir - 1], np.eye(4), atol=1e-5)

    # test now with multiple spliceables input to segment
    Nexbody = 3
    seg = Segment([spliceable] * Nexbody, 'N', 'C')
    Npairs_expected = Nexbody * Nexsite**2 * Npairs0
    assert seg.x2exit.shape == (Npairs_expected, 4, 4)
    assert seg.x2orgn.shape == (Npairs_expected, 4, 4)
    assert len(seg.entryresid) == Npairs_expected
    assert len(seg.exitresid) == Npairs_expected
    assert len(seg.bodyid) == Npairs_expected
    for i in range(Nexbody):
        assert i == seg.bodyid[0 + i * Npairs0 * Nexsite**2]
    assert np.all(seg.x2exit[..., 3, :3] == 0)
    assert np.all(seg.x2exit[..., 3, 3] == 1)
    for e2x, e2o, ir, jr in zip(seg.x2exit, seg.x2orgn, seg.entryresid,
                                seg.exitresid):
        assert np.allclose(stubs[ir - 1] @ e2o, np.eye(4), atol=1e-5)
        assert np.allclose(stubs[ir - 1] @ e2x, stubs[jr - 1], atol=1e-5)


@only_if_pyrosetta
def test_Segment_make_head_tail(c1pose):
    helix = Spliceable(c1pose, [(':3', 'N'), ('-4:', 'C')])
    seg = Segment([helix], 'NC')
    assert len(seg) == 12
    assert len(seg.make_head()) == 3
    assert len(seg.make_tail()) == 4
    assert np.all(seg.make_head().entryresid != -1)
    assert np.all(seg.make_head().exitresid == -1)
    assert np.all(seg.make_tail().entryresid == -1)
    assert np.all(seg.make_tail().exitresid != -1)


@only_if_pyrosetta
def test_Segments_split_at(c1pose):
    helix = Spliceable(c1pose, [(':3', 'N'), ('-4:', 'C')])
    segs = Segments([Segment([helix], '_C')] + [Segment([helix], 'NC')] * 4 +
                    [Segment([helix], 'N_')])
    assert len(segs) == 6
    tail, head = segs.split_at(2)
    assert len(tail) == 3
    assert len(head) == 4
    assert tail[0].entrypol is None
    assert tail[-1].exitpol is None
    assert head[0].entrypol is None
    assert head[-1].exitpol is None
