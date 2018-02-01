import multiprocessing
import os
import itertools as it
from collections.abc import Iterable
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from numpy.linalg import inv
from .criteria import CriteriaList
try:
    from pyrosetta import rosetta as ros
    from pyrosetta.rosetta.core import scoring
    rm_lower_t = ros.core.pose.remove_lower_terminus_type_from_pose_residue
    rm_upper_t = ros.core.pose.remove_upper_terminus_type_from_pose_residue
except ImportError:
    print('!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')
    print('pyrosetta not available, worms won\'t work')
    print('!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')
from . import util


class SpliceSite:

    def __init__(self, sele, polarity, chain=None):
        if isinstance(sele, str) or isinstance(sele, int):
            sele = [sele]
        self.selections = list(sele)
        assert polarity in ('N', 'C', None)
        self.polarity = polarity
        self.chain = chain

    def resid(self, id, pose):
        resid = id if id >= 0 else len(pose) + 1 + id
        if not 0 < resid <= len(pose):
            raise ValueError('resid ' + str(resid)
                             + ' invalid for pose of size '
                             + str(len(pose)))
        return resid

    def resids_impl(self, sele, spliceable):
        if isinstance(sele, int):
            if self.chain is None:
                return set([self.resid(sele, spliceable.body)])
            else:
                ir = self.resid(sele, spliceable.chains[self.chain])
                ir += spliceable.start_of_chain[self.chain]
                return set([ir])
        elif isinstance(sele, str):
            x = sele.split(',')
            s = x[-1].split(':')
            chain = int(x[0]) if len(x) == 2 else None
            pose = spliceable.chains[chain] if chain else spliceable.body
            start = self.resid(int(s[0] or 1), pose)
            stop = self.resid(int(s[1] or -1), pose)
            step = int(s[2]) if len(s) > 2 else 1
            # print(start, stop + 1, step)
            resids = set()
            for ir in range(start, stop + 1, step):
                assert 0 < ir <= len(pose)
                resids.add(spliceable.start_of_chain[chain] + ir)
            return resids
        elif sele is None:
            return set([None])
        else:
            raise ValueError('selection must be int, str, or None')

    def resids(self, spliceabe):
        resids = set()
        for sele in self.selections:
            try:
                resids |= self.resids_impl(sele, spliceabe)
            except ValueError as e:
                raise ValueError('Error with selection '
                                 + str(sele) + ': ' + str(e))
        resids = sorted(resids)
        if not resids:
            raise ValueError('empty SpliceSite')
        return resids

    def __repr__(self):
        c = '' if self.chain is None else ', chain=' + str(self.chain)
        return 'SpliceSite(' + str(self.selections) + \
            ', ' + self.polarity + c + ')'


class Spliceable:

    def __init__(self, body, sites, *, bodyid=None, min_seg_len=1):
        self.body = body
        chains = list(body.split_by_chain())
        self.start_of_chain = {i + 1: sum(len(c) for c in chains[:i])
                               for i in range(len(chains))}
        self.end_of_chain = {i + 1: sum(len(c) for c in chains[:i + 1])
                             for i in range(len(chains))}
        self.start_of_chain[None] = 0
        self.chains = {i + 1: c for i, c in enumerate(chains)}
        self.bodyid = bodyid
        if callable(sites):
            sites = sites(body)
        if isinstance(sites, SpliceSite):
            sites = [sites]
        self.sites = list(sites)
        for i, site in enumerate(self.sites):
            if isinstance(site, str):
                raise ValueError('site currently must be (sele, polarity)')
            if not isinstance(site, SpliceSite):
                if isinstance(site, dict):
                    self.sites[i] = SpliceSite(**site)
                else:
                    if not isinstance(site, Iterable):
                        self.sites[i] = (site,)
                    self.sites[i] = SpliceSite(*site)
        self.nsite = dict(N=0, C=0)
        for s in self.sites: self.nsite[s.polarity] += 1
        self.min_seg_len = min_seg_len

    def spliceable_positions(self):
        """selection of resids, and map 'global' index to selected index"""
        resid_subset = set()
        for site in self.sites:
            resid_subset |= set(site.resids(self))
        resid_subset = np.array(list(resid_subset))
        # really? must be an easier way to 'invert' a mapping in numpy?
        N = len(self.body) + 1
        val, idx = np.where(0 == (np.arange(N)[np.newaxis, :] -
                                  resid_subset[:, np.newaxis]))
        to_subset = np.array(N * [-1])
        to_subset[idx] = val
        assert np.all(to_subset[resid_subset] == np.arange(len(resid_subset)))
        return resid_subset, to_subset

    def is_compatible(self, isite, ires, jsite, jres):
        if ires < 0 or jres < 0: return True
        assert 0 < ires <= len(self.body) and 0 < jres <= len(self.body)
        ichain, jchain = self.body.chain(ires), self.body.chain(jres)
        if ichain == jchain:
            ipol = self.sites[isite].polarity
            jpol = self.sites[jsite].polarity
            if ipol == jpol: return False
            if ipol == 'N': seglen = jres - ires + 1
            else: seglen = ires - jres + 1
            if seglen < self.min_seg_len: return False
        return True

    def __repr__(self):
        return ('Spliceable: body=(' + str(len(self.body)) + ',' +
                str(self.body).splitlines()[0].split('/')[-1] +
                '), sites=' + str([(s.resids(self), s.polarity) for s in self.sites]))


class AnnoPose:

    def __init__(self, pose, iseg, srcpose, src_lb, src_ub, cyclic_entry):
        self.pose = pose
        self.iseg = iseg
        self.srcpose = srcpose
        self.src_lb = src_lb
        self.src_ub = src_ub
        self.cyclic_entry = cyclic_entry

    def __iter__(self):
        yield self.pose
        yield (self.iseg, self.srcpose, self.src_lb, self.src_ub)

    def __getitem__(self, i):
        if i is 0: return self.pose
        if i is 1: return (self.iseg, self.srcpose, self.src_lb, self.src_ub)

    def seq(self):
        return self.pose.sequence()

    def srcseq(self):
        return self.srcpose.sequence()[self.src_lb - 1:self.src_ub]


class Segment:

    def __init__(self, spliceables, entry=None, exit=None, expert=False):
        if entry and len(entry) is 2:
            entry, exit = entry
            if entry == '_': entry = None
            if exit == '_': exit = None
        self.entrypol = entry or None
        self.exitpol = exit or None
        self.min_sites = dict(C=9e9, N=9e9)
        self.max_sites = dict(C=0, N=0)
        if not spliceables:
            raise ValueError('spliceables must not be empty, spliceables ='
                             + str(spliceables))
        for s in spliceables:
            if not isinstance(s, Spliceable):
                raise ValueError('Segment only accepts list of Spliceable')
        self.init_segment(spliceables, entry, exit)
        self.nchains = len(spliceables[0].chains)
        for s in spliceables:
            if not expert and len(s.chains) is not self.nchains:
                raise ValueError('different number of chains for spliceables',
                                 ' in segment (pass expert=True to ignore)')
            self.nchains = max(self.nchains, len(s.chains))

    def __len__(self):
        return len(self.bodyid)

    def init_segment(self, spliceables=None, entry=None, exit=None):
        if not (entry or exit):
            raise ValueError('at least one of entry/exit required')
        self.spliceables = list(spliceables) or self.spliceables
        self.entrypol = entry or self.entrypol or None
        self.exitpol = exit or self.exitpol or None
        # each array has all in/out pairs
        self.x2exit, self.x2orgn, self.bodyid = [], [], []
        self.entryresid, self.exitresid = [], []
        self.entrysiteid, self.exitsiteid = [], []
        # this whole loop is pretty inefficient, but that probably
        # doesn't matter much given the cost subsequent operations (?)
        for ibody, spliceable in enumerate(self.spliceables):
            for p in 'NC':
                self.min_sites[p] = min(self.min_sites[p], spliceable.nsite[p])
                self.max_sites[p] = max(self.max_sites[p], spliceable.nsite[p])
            resid_subset, to_subset = spliceable.spliceable_positions()
            bodyid = ibody if spliceable.bodyid is None else spliceable.bodyid
            # extract 'stubs' from body at selected positions
            # rif 'stubs' have 'extra' 'features'... the raw field is
            # just bog-standard homogeneous matrices
            # stubs = rcl.bbstubs(spliceable.body, resid_subset)['raw']
            # stubs = stubs.astype('f8')
            stubs = util.get_bb_stubs(spliceable.body, resid_subset)
            if len(resid_subset) != stubs.shape[0]:
                raise ValueError("no funny residues supported")
            stubs_inv = inv(stubs)
            entry_sites = (list(enumerate(spliceable.sites)) if self.entrypol else
                           [(-1, SpliceSite(sele=[None], polarity=self.entrypol))])
            exit_sites = (list(enumerate(spliceable.sites)) if self.exitpol else
                          [(-1, SpliceSite(sele=[None], polarity=self.exitpol))])
            for isite, entry_site in entry_sites:
                if entry_site.polarity == self.entrypol:
                    for jsite, exit_site in exit_sites:
                        if isite != jsite and exit_site.polarity == self.exitpol:
                            for ires in entry_site.resids(spliceable):
                                istub_inv = (np.eye(4) if not ires
                                             else stubs_inv[to_subset[ires]])
                                ires = ires or -1
                                for jres in exit_site.resids(spliceable):
                                    jstub = (np.eye(4) if not jres
                                             else stubs[to_subset[jres]])
                                    jres = jres or -1
                                    if not spliceable.is_compatible(
                                            isite, ires, jsite, jres):
                                        continue
                                    self.x2exit.append(istub_inv @ jstub)
                                    self.x2orgn.append(istub_inv)
                                    self.entrysiteid.append(isite)
                                    self.entryresid.append(ires)
                                    self.exitsiteid.append(jsite)
                                    self.exitresid.append(jres)
                                    self.bodyid.append(bodyid)
        if len(self.x2exit) is 0:
            raise ValueError('no valid splices found')
        self.x2exit = np.stack(self.x2exit)
        self.x2orgn = np.stack(self.x2orgn)
        self.entrysiteid = np.stack(self.entrysiteid)
        self.entryresid = np.array(self.entryresid)
        self.exitsiteid = np.array(self.exitsiteid)
        self.exitresid = np.array(self.exitresid)
        self.bodyid = np.array(self.bodyid)

    def same_bodies_as(self, other):
        bodies1 = [s.body for s in self.spliceables]
        bodies2 = [s.body for s in other.spliceables]
        return bodies1 == bodies2

    def make_pose_chains(self, indices, position=None, pad=(0, 0), iseg=None,
                         segments=None, cyclictrim=None):
        """what a monster this has become. returns (segchains, rest)
        segchains elems are [enterexitchain] or, [enterchain, ..., exitchain]
        rest holds other chains IFF enter and exit in same chain
        each element is a pair [pose, source] where source is
        (origin_pose, start_res, stop_res)
        cyclictrim specifies segments which are spliced across the
        symmetric interface. segments only needed if cyclictrim==True
        if cyclictrim, last segment will only be a single entry residue
        """
        if isinstance(indices, int):
            assert not cyclictrim
            index = indices
        else: index = indices[iseg]
        spliceable = self.spliceables[self.bodyid[index]]
        pose, chains = spliceable.body, spliceable.chains
        ir_en, ir_ex = self.entryresid[index], self.exitresid[index]
        cyclic_entry = defaultdict(lambda: None)
        if cyclictrim and cyclictrim[1] < 0:
            cyclictrim = cyclictrim[0], cyclictrim[1] + len(segments)
        if cyclictrim and iseg == cyclictrim[0]:
            assert ir_en == -1, 'paece sign not implemented yet'
            ir_en = segments[cyclictrim[1]].entryresid[indices[cyclictrim[1]]]
            # annotate enex entries with cyclictrim info
            cyclic_entry[pose.chain(ir_en)] = iseg, ir_en
        if cyclictrim and iseg == cyclictrim[1]:
            assert ir_ex == -1
            assert iseg + 1 == len(segments)
            i = ir_en
            p = util.subpose(pose, i, i)
            if position is not None: util.xform_pose(position, p)
            return [AnnoPose(p, iseg, pose, i, i, None)], []
        ch_en = pose.chain(ir_en) if ir_en > 0 else None
        ch_ex = pose.chain(ir_ex) if ir_ex > 0 else None
        pl_en, pl_ex = self.entrypol, self.exitpol
        if cyclictrim and iseg == 0:
            pl_en = segments[-1].entrypol
        if cyclictrim and iseg + 1 == len(segments):
            assert 0
            pl_ex = segments[0].exitpol
        if ch_en: ir_en -= spliceable.start_of_chain[ch_en]
        if ch_ex: ir_ex -= spliceable.start_of_chain[ch_ex]
        assert ch_en or ch_ex
        rest = {chains[i]: AnnoPose(chains[i], iseg, pose,
                                    spliceable.start_of_chain[i] + 1,
                                    spliceable.end_of_chain[i],
                                    cyclic_entry[i])
                for i in range(1, len(chains) + 1)}
        for ap in rest.values():
            assert ap.pose.sequence() == ap.srcpose.sequence()[
                ap.src_lb - 1:ap.src_ub]
        if ch_en: del rest[chains[ch_en]]
        if ch_en == ch_ex:
            assert len(rest) + 1 == len(chains)
            p, l1, u1 = util.trim_pose(chains[ch_en], ir_en, pl_en, pad[0])
            iexit1 = ir_ex - (pl_ex == 'C') * (len(chains[ch_en]) - len(p))
            p, l2, u2 = util.trim_pose(p, iexit1, pl_ex, pad[1] - 1)
            lb = l1 + l2 - 1 + spliceable.start_of_chain[ch_en]
            ub = l1 + u2 - 1 + spliceable.start_of_chain[ch_en]
            enex = [AnnoPose(p, iseg, pose, lb, ub, cyclic_entry[ch_en])]
            assert p.sequence() == pose.sequence()[lb - 1:ub]
            rest = list(rest.values())
        else:
            if ch_ex: del rest[chains[ch_ex]]
            p_en = [chains[ch_en]] if ch_en else []
            p_ex = [chains[ch_ex]] if ch_ex else []
            if p_en:
                p, lben, uben = util.trim_pose(
                    p_en[0], ir_en, pl_en, pad[0])
                lb = lben + spliceable.start_of_chain[ch_en]
                ub = uben + spliceable.start_of_chain[ch_en]
                p_en = [AnnoPose(p, iseg, pose, lb, ub, cyclic_entry[ch_en])]
                assert p.sequence() == pose.sequence()[lb - 1:ub]
            if p_ex:
                p, lbex, ubex = util.trim_pose(
                    p_ex[0], ir_ex, pl_ex, pad[1] - 1)
                lb = lbex + spliceable.start_of_chain[ch_ex]
                ub = ubex + spliceable.start_of_chain[ch_ex]
                p_ex = [AnnoPose(p, iseg, pose, lb, ub, cyclic_entry[ch_ex])]
                assert p.sequence() == pose.sequence()[lb - 1:ub]
            enex = p_en + list(rest.values()) + p_ex
            rest = []
        if position is not None:
            position = util.rosetta_stub_from_numpy_stub(position)
            for x in enex: x.pose = x.pose.clone()
            for x in rest: x.pose = x.pose.clone()
            for ap in it.chain(enex, rest):
                ros.protocols.sic_dock.xform_pose(ap.pose, position)
        for ap in it.chain(enex, rest):
            assert isinstance(ap, AnnoPose)
            assert ap.iseg == iseg
            assert ap.seq() == ap.srcseq()
        return enex, rest


def _cyclic_permute_chains(chainslist, polarity):
    chainslist_beg = 0
    beg, end = chainslist[chainslist_beg], chainslist[-1]
    if chainslist_beg != 0:
        raise NotImplementedError('peace sign not working yet')
    n2c = (polarity == 'N')
    if n2c:
        stub1 = util.get_bb_stubs(beg[0][0], [1])
        stub2 = util.get_bb_stubs(end[-1][0], [1])
        rm_lower_t(beg[0][0], 1)
        end = end[:-1]
    else:
        # from . import vis
        # for i, b in enumerate(beg): vis.showme(b[0], name='beg_%i' % i)
        # for i, e in enumerate(end): vis.showme(e[0], name='end_%i' % i)
        stub1 = util.get_bb_stubs(beg[-1][0], [len(beg[-1][0])])
        stub2 = util.get_bb_stubs(end[0][0], [1])
        rm_upper_t(beg[-1][0], len(beg[-1][0]))
        assert len(end[0][0]) == 1
        end = end[1:]
    xalign = stub1[0] @ np.linalg.inv(stub2[0])
    print(xalign.shape)
    for p in end: util.xform_pose(xalign, p[0])
    if n2c: chainslist[chainslist_beg] = end + beg
    else: chainslist[chainslist_beg] = beg + end
    chainslist[-1] = []


def reorder_spliced_as_N_to_C(body_chains, polarities):
    "remap chains of each body such that concatenated chains are N->C"
    if len(body_chains) != len(polarities) + 1:
        raise ValueError('must be one more body_chains than polarities')
    chains, pol = [[]], {}
    if not all(0 < len(dg) for dg in body_chains):
        raise ValueError('body_chains values must be [enterexit], '
                         '[enter,exit], or [enter, ..., exit')
    for i in range(1, len(polarities)):
        if len(body_chains[i]) == 1:
            if polarities[i - 1] != polarities[i]:
                raise ValueError('polarity mismatch on single chain connect')
    for i, dg in enumerate(body_chains):
        chains[-1].append(dg[0])
        if i != 0: pol[len(chains) - 1] = polarities[i - 1]
        if len(dg) > 1: chains.extend([x] for x in dg[1:])
    for i, chain in enumerate(chains):
        if i in pol and pol[i] == 'C':
            chains[i] = chains[i][::-1]
    return chains


class Worms:

    def __init__(self, segments, scores, indices, positions, criteria, detail):
        self.segments = segments
        self.scores = scores
        self.indices = indices
        self.positions = positions
        self.criteria = criteria
        self.detail = detail
        self.score0 = scoring.ScoreFunctionFactory.create_score_function(
            'score0')
        self.score0sym = scoring.symmetry.symmetrize_scorefunction(self.score0)
        self.splicepoint_cache = {}

    def pose(self, which, *, align=True, end=None, only_connected='auto',
             join=True, cyclic_permute=None, provenance=False, **kw):
        "makes a pose for the ith worm"
        if isinstance(which, Iterable): return (
            self.pose(w, align=align, end=end, join=join,
                      only_connected=only_connected, **kw)
            for w in which)
        # print("Will needs to fix bb O/H position!")
        rm_lower_t = ros.core.pose.remove_lower_terminus_type_from_pose_residue
        rm_upper_t = ros.core.pose.remove_upper_terminus_type_from_pose_residue
        if end is None and cyclic_permute is None:
            cyclic_permute = self.criteria.is_cyclic
            end = True
        if end is None:
            end = not self.criteria.is_cyclic or cyclic_permute
        if only_connected is None:
            only_connected = not self.criteria.is_cyclic
        if cyclic_permute is None:
            cyclic_permute = not end
        elif cyclic_permute and not self.criteria.is_cyclic:
            raise ValueError('cyclic_permute should only be used for Cyclic')
        if cyclic_permute:
            cyclic_permute = self.criteria.from_seg, self.criteria.to_seg
        iend = None if end else -1
        entryexits = [seg.make_pose_chains(self.indices[which],
                                           self.positions[which][iseg],
                                           iseg=iseg,
                                           segments=self.segments,
                                           cyclictrim=cyclic_permute)
                      for iseg, seg in enumerate(self.segments[:iend])]
        entryexits, rest = zip(*entryexits)
        for ap in it.chain(*entryexits, *rest):
            assert isinstance(ap, AnnoPose)
        chainslist = reorder_spliced_as_N_to_C(
            entryexits, [s.entrypol for s in self.segments[1:iend]])
        if align:
            x = self.criteria.alignment(segpos=self.positions[which], **kw)
            for ap in it.chain(*chainslist, *rest): util.xform_pose(x, ap.pose)
        if cyclic_permute and len(chainslist) > 1:
            cyclic_entry_count = 0
            for ap in it.chain(*entryexits, *rest):
                cyclic_entry_count += (ap.cyclic_entry is not None)
            assert cyclic_entry_count == 1
            _cyclic_permute_chains(chainslist, self.segments[-1].entrypol)
            assert len(chainslist[-1]) == 0
            chainslist = chainslist[:-1]
        sourcelist = [[x[1] for x in c] for c in chainslist]
        chainslist = [[x[0] for x in c] for c in chainslist]
        pose = ros.core.pose.Pose()
        prov0 = []
        splicepoints = []
        for chains, sources in zip(chainslist, sourcelist):
            if (only_connected and len(chains) is 1 and
                    (end or chains is not chainslist[-1])):
                skipsegs = ((self.criteria.to_seg, self.criteria.from_seg)
                            if not self.criteria.is_cyclic else [])
                skipsegs = [len(self.segments) - 1 if x is -1 else x
                            for x in skipsegs]
                if ((only_connected == 'auto' and sources[0][0] in skipsegs)
                    or only_connected != 'auto'): continue
            ros.core.pose.append_pose_to_pose(pose, chains[0], True)
            prov0.append(sources[0])
            for chain, source in zip(chains[1:], sources[1:]):
                assert isinstance(chain, ros.core.pose.Pose)
                rm_upper_t(pose, len(pose))
                rm_lower_t(chain, 1)
                splicepoints.append(len(pose))
                ros.core.pose.append_pose_to_pose(pose, chain, not join)
                prov0.append(source)
        self.splicepoint_cache[which] = splicepoints
        if not only_connected or only_connected == 'auto':
            for chain, source in it.chain(*rest):
                assert isinstance(chain, ros.core.pose.Pose)
                ros.core.pose.append_pose_to_pose(pose, chain, True)
                prov0.append(source)
        assert util.worst_CN_connect(pose) < 0.5
        assert util.no_overlapping_residues(pose)
        if not provenance: return pose
        prov = []
        for i, pr in enumerate(prov0):
            iseg, psrc, lb0, ub0 = pr
            lb1 = sum(ub - lb + 1 for _, _, lb, ub in prov0[:i]) + 1
            ub1 = lb1 + ub0 - lb0
            if ub0 == lb0:
                assert cyclic_permute
                continue
            assert ub0 - lb0 == ub1 - lb1
            assert 0 < lb0 <= len(psrc) and 0 < ub0 <= len(psrc)
            assert 0 < lb1 <= len(pose) and 0 < ub1 <= len(pose)
            assert psrc.sequence()[lb0 - 1:ub0] == pose.sequence()[lb1 - 1:ub1]
            prov.append((lb1, ub1, psrc, lb0, ub0))
        return pose, prov

    def splicepoints(self, which):
        if not which in self.splicepoint_cache:
            self.pose(which)
        assert isinstance(which, int)
        return self.splicepoint_cache[which]

    def clear_caches(self):
        self.splicepoint_cache = {}

    def sympose(self, which, score=False, provenance=False, *, fullatom=False,
                parallel=False, asym_score_thresh=50):
        if isinstance(which, Iterable):
            which = list(which)
            if not all(0 <= i < len(self) for i in which):
                raise IndexError('invalid worm index')
            if parallel:
                with ThreadPoolExecutor() as pool:
                    result = pool.map(self.sympose, which, it.repeat(score),
                                      it.repeat(provenance))
                    return list(result)
            else: return list(map(self.sympose, which, it.repeat(score), it.repeat(provenance)))
        if not 0 <= which < len(self):
            raise IndexError('invalid worm index')
        p, prov = self.pose(which, provenance=True)
        if fullatom: pfull = p.clone()
        pcen = p
        # todo: why is asym scoring broken?!?
        # try: score0asym = self.score0(p)
        # except: score0asym = 9e9
        # if score0asym > asym_score_thresh:
        # return None, None if score else None
        ros.core.util.switch_to_residue_type_set(pcen, 'centroid')
        symdata = util.get_symdata(self.criteria.symname)
        sfxn = self.score0sym
        if symdata is None: sfxn = self.score0
        else: ros.core.pose.symmetry.make_symmetric_pose(pcen, symdata)
        if fullatom:
            if symdata is not None:
                ros.core.pose.symmetry.make_symmetric_pose(pfull, symdata)
            p = pfull
        else:
            p = pcen
        if score and provenance:
            return p, sfxn(pcen), prov
        if score:
            return p, sfxn(pcen)
        if provenance:
            return p, prov
        return p

    def splices(self, which):
        if isinstance(which, Iterable): return (self.splices(w) for w in which)
        splices = []
        for i in range(len(self.segments) - 1):
            seg1 = self.segments[i]
            isegchoice1 = self.indices[which, i]
            ibody1 = seg1.bodyid[isegchoice1]
            spliceable1 = seg1.spliceables[ibody1]
            resid1 = seg1.exitresid[isegchoice1]
            ichain1 = spliceable1.body.chain(resid1)
            chainresid1 = resid1 - spliceable1.start_of_chain[ichain1]
            seg2 = self.segments[i + 1]
            isegchoice2 = self.indices[which, i + 1]
            ibody2 = seg2.bodyid[isegchoice2]
            spliceable2 = seg2.spliceables[ibody2]
            resid2 = seg2.entryresid[isegchoice2]
            ichain2 = spliceable2.body.chain(resid2)
            chainresid2 = resid2 - spliceable2.start_of_chain[ichain2]
            drn = self.segments[i].exitpol + self.segments[i + 1].entrypol
            splices.append((ibody1, ichain1, chainresid1,
                            ibody2, ichain2, chainresid2, drn))
        return splices

    def __len__(self): return len(self.scores)

    def __getitem__(self, i):
        return (i, self.scores[i],) + self.sympose(i, score=True)


def _chain_xforms(segments):
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    x2exit = [s.x2exit for s in segments]
    x2orgn = [s.x2orgn for s in segments]
    fullaxes = (np.newaxis,) * (len(x2exit) - 1)
    xconn = [x2exit[0][fullaxes], ]
    xbody = [x2orgn[0][fullaxes], ]
    for iseg in range(1, len(x2exit)):
        fullaxes = (slice(None),) + (np.newaxis,) * iseg
        xconn.append(xconn[iseg - 1] @ x2exit[iseg][fullaxes])
        xbody.append(xconn[iseg - 1] @ x2orgn[iseg][fullaxes])
    perm = list(range(len(xbody) - 1, -1, -1)) + [len(xbody), len(xbody) + 1]
    xbody = [np.transpose(x, perm) for x in xbody]
    xconn = [np.transpose(x, perm) for x in xconn]
    return xbody, xconn


def _grow_chunk(samp, segpos, conpos, segs, end, criteria, thresh, matchlast):
    ML = matchlast
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    # body must match, and splice sites must be distinct
    if ML is not None:
        ndimchunk = segpos[0].ndim - 2
        bidB = segs[-1].bodyid[samp[-1]]
        site3 = segs[-1].entrysiteid[samp[-1]]
        if ML < ndimchunk:
            bidA = segs[ML].bodyid
            site1 = segs[ML].entrysiteid
            site2 = segs[ML].exitsiteid
            allowed = (bidA == bidB) * (site1 != site3) * (site2 != site3)
            idx = (slice(None),) * ML + (allowed,)
            segpos = segpos[: ML] + [x[idx] for x in segpos[ML:]]
            conpos = conpos[: ML] + [x[idx] for x in conpos[ML:]]
            idxmap = np.where(allowed)[0]
        else:
            bidA = segs[ML].bodyid[samp[ML - ndimchunk]]
            site1 = segs[ML].entrysiteid[samp[ML - ndimchunk]]
            site2 = segs[ML].exitsiteid[samp[ML - ndimchunk]]
            if bidA != bidB or site3 == site2 or site3 == site1:
                return
    segpos, conpos = segpos[:end], conpos[:end]
    for iseg, seg in enumerate(segs[end:]):
        segpos.append(conpos[-1] @ seg.x2orgn[samp[iseg]])
        if seg is not segs[-1]:
            conpos.append(conpos[-1] @ seg.x2exit[samp[iseg]])
    score = criteria.score(segpos=segpos)
    ilow0 = np.where(score < thresh)
    sampidx = tuple(np.repeat(i, len(ilow0[0])) for i in samp)
    lowpostmp = []
    for iseg in range(len(segpos)):
        ilow = ilow0[: iseg + 1] + (0,) * (segpos[0].ndim - 2 - (iseg + 1))
        lowpostmp.append(segpos[iseg][ilow])
    ilow1 = (ilow0 if (ML is None or ML >= ndimchunk) else
             ilow0[:ML] + (idxmap[ilow0[ML]],) + ilow0[ML + 1:])
    return score[ilow0], np.array(ilow1 + sampidx).T, np.stack(lowpostmp, 1)


def _grow_chunks(ijob, context):
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    sampsizes, njob, segments, end, criteria, thresh, matchlast = context
    samples = it.product(*(range(n) for n in sampsizes))
    segpos, connpos = _chain_xforms(segments[:end])  # common data
    args = [list(samples)[ijob::njob]] + [it.repeat(x) for x in (
        segpos, connpos, segments, end, criteria, thresh, matchlast)]
    chunks = list(map(_grow_chunk, *args))
    chunks = [c for c in chunks if c is not None]
    return [np.concatenate([c[i] for c in chunks])
            for i in range(3)] if chunks else None


def _check_topology(segments, criteria, expert=False):
    if segments[0].entrypol is not None:
        raise ValueError('beginning of worm cant have entry')
    if segments[-1].exitpol is not None:
        raise ValueError('end of worm cant have exit')
    for a, b in zip(segments[:-1], segments[1:]):
        if not (a.exitpol and b.entrypol and a.exitpol != b.entrypol):
            raise ValueError('incompatible exit->entry polarity: '
                             + str(a.exitpol) + '->'
                             + str(b.entrypol) + ' on segment pair: '
                             + str((segments.index(a), segments.index(b))))
    matchlast = criteria.last_body_same_as
    if matchlast is not None and not expert and (
            not segments[matchlast].same_bodies_as(segments[-1])):
        raise ValueError("segments[matchlast] not same as segments[-1], "
                         + "if you're sure, pass expert=True")
    if criteria.is_cyclic and not criteria.to_seg in (-1, len(segments) - 1):
        raise ValueError('Cyclic and to_seg is not last segment,'
                         'if you\'re sure, pass expert=True')
    if criteria.is_cyclic:
        beg, end = segments[criteria.from_seg], segments[criteria.to_seg]
        sites_required = {'N': 0, 'C': 0, None: 0}
        sites_required[beg.entrypol] += 1
        sites_required[beg.exitpol] += 1
        sites_required[end.entrypol] += 1
        # print('pols', beg.entrypol, beg.exitpol, end.entrypol)
        for pol in 'NC':
            # print(pol, beg.max_sites[pol], sites_required[pol])
            if beg.max_sites[pol] < sites_required[pol]:
                msg = 'Not enough %s sites in any of segment %i Spliceables, %i required, at most %i available' % (
                    pol, criteria.from_seg, sites_required[pol],
                    beg.max_sites[pol])
                raise ValueError(msg)
            if beg.min_sites[pol] < sites_required[pol]:
                msg = 'Not enough %s sites in all of segment %i Spliceables, %i required, some have only %i available (pass expert=True if you really want to run anyway)' % (
                    pol, criteria.from_seg, sites_required[pol],
                    beg.max_sites[pol])
                if not expert: raise ValueError(msg)
                print("WARNING:", msg)
    return matchlast


def grow(segments, criteria, *, thresh=2, expert=0, memsize=1e6,
         executor=None, max_workers=None, verbosity=0, jobmult=128,
         chunklim=None):
    if verbosity > 0:
        print('grow, from', criteria.from_seg, 'to', criteria.to_seg)
        for i, seg in enumerate(segments):
            print(' segment', i, 'enter:', seg.entrypol, 'exit:', seg.exitpol)
            for sp in seg.spliceables: print('   ', sp)
    if not isinstance(criteria, CriteriaList):
        criteria = CriteriaList(criteria)
    # checks and setup
    matchlast = _check_topology(segments, criteria, expert)
    if executor is None:
        executor = ThreadPoolExecutor  # todo: some kind of null executor?
        max_workers = 1
    if max_workers is None: max_workers = util.cpu_count()
    sizes = [len(s.bodyid) for s in segments]
    end = len(segments) - 1
    while end > 1 and (np.prod(sizes[end:]) < max_workers or
                       memsize <= 64 * np.prod(sizes[:end])): end -= 1
    ntot, chunksize, nchunks = (np.product(x)
                                for x in (sizes, sizes[:end], sizes[end:]))
    nworker = max_workers or util.cpu_count()
    njob = nworker * jobmult
    njob = min(njob, nchunks)
    if verbosity >= 0:
        print('tot: {:,} chunksize: {:,} nchunks: {:,} nworker: {} '
              'njob: {} worm/job: {:,} chunk/job: {} sizes={}'.format(
                  ntot, chunksize, nchunks, nworker, njob,
                  ntot / njob, nchunks / njob, sizes))

    # run the stuff
    tmp = [s.spliceables for s in segments]
    for s in segments: s.spliceables = None  # poses not pickleable...
    with executor(max_workers=nworker) as pool:
        context = (sizes[end:], njob, segments, end, criteria, thresh,
                   matchlast)
        args = [range(njob)] + [it.repeat(context)]
        chunks = util.tqdm_parallel_map(
            pool, _grow_chunks, *args,
            unit='K worms', ascii=0, desc='growing worms',
            unit_scale=int(ntot / njob / 1000), disable=verbosity < 0)
        chunks = [x for x in chunks if x is not None]
    for s, t in zip(segments, tmp): s.spliceables = t  # put the poses back

    # compose and sort results
    scores = np.concatenate([c[0] for c in chunks])
    order = np.argsort(scores)
    scores = scores[order]
    lowidx = np.concatenate([c[1] for c in chunks])[order]
    lowpos = np.concatenate([c[2] for c in chunks])[order]
    lowposlist = [lowpos[:, i] for i in range(len(segments))]
    score_check = criteria.score(segpos=lowposlist, verbosity=verbosity)
    assert np.allclose(score_check, scores)
    detail = dict(ntot=ntot, chunksize=chunksize, nchunks=nchunks,
                  nworker=nworker, njob=njob, sizes=sizes, end=end)
    return Worms(segments, scores, lowidx, lowpos, criteria, detail)
