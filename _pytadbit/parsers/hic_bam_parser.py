"""
parser provided by Enrique Vidal <enrique.vidal@crg.eu> to read write 2D beds
into compressed BAM format.

"""

from cPickle                      import load, dump
from time                         import sleep, time
from collections                  import OrderedDict
from subprocess                   import Popen, PIPE
from random                       import getrandbits
from tarfile                      import open as taropen
from StringIO                     import StringIO
from warnings                     import warn
import datetime
from sys                          import stdout, stderr, exc_info
import os
import multiprocessing as mu

try:
    from lockfile                 import LockFile
except ImportError:
    pass  # silently pass, very specific need

from pysam                        import view, AlignmentFile

from pytadbit.utils.file_handling import mkdir
from pytadbit.utils.extraviews    import nicer
from pytadbit.mapping.filter      import MASKED



def filters_to_bin(filters):
    return sum((k in filters) * 2**(k-1) for k in MASKED)


def printime(msg):
    print (msg +
           (' ' * (79 - len(msg.replace('\n', '')))) +
           '[' +
           str(datetime.datetime.fromtimestamp(time()).strftime('%Y-%m-%d %H:%M:%S')) +
           ']')


def print_progress(procs):
    stdout.write('     ')
    prev_done = done = i = 0
    while done < len(procs):
        sleep(2)
        done = sum(p.ready() for p in procs)
        for i in range(prev_done, done):
            if not i % 10 and i:
                stdout.write(' ')
            if not i % 50 and i:
                stdout.write(' %9s\n     ' % ('%s/%s' % (i , len(procs))))
            stdout.write('.')
            stdout.flush()
        prev_done = done
    print '%s %9s\n' % (' ' * (54 - (i % 50) - (i % 50) / 10),
                        '%s/%s' % (len(procs),len(procs)))


def _map2sam_short(line, flag):
    """
    translate map + flag into hic-sam (two lines per contact)
    lose information
    58% of the size using RE sites, and 68% of the generation time
    """
    (qname,
     rname, pos, _, _, _, _,
     rnext, pnext, _) = line.strip().split('\t', 9)

    # multicontact?
    try:
        tc = qname.split('#')[1].split('/')[1]
    except IndexError:
        tc = '1'
    # trans contact?
    if rname != rnext:
        flag += 1024 # filter_keys['trans-chromosomic'] = 2**10
    r1r2 = ('{0}\t{1}\t{2}\t{3}\t0\t1P\t{4}\t{5}\t*\t*\t*\t'
            'TC:i:{6}\n'
            '{0}\t{1}\t{4}\t{5}\t0\t1S\t{2}\t{3}\t*\t*\t*\t'
            'TC:i:{6}\n'
           ).format(
               qname,               # 0
               flag,                # 1
               rname,               # 2
               pos,                 # 3
               rnext,               # 4
               pnext,               # 5
               tc)                  # 6
    return r1r2


def _map2sam_mid(line, flag):
    """
    translate map + flag into hic-sam (two lines per contact)
    only loses RE sites, that can be added back later
    63% of the size using RE sites, and 72% of the generation time
    """
    (qname,
     rname, pos, s1, l1, _, _,
     rnext, pnext, s2, l2, _) = line.strip().split('\t', 11)

    # multicontact?
    try:
        tc = qname.split('#')[1].split('/')[1]
    except IndexError:
        tc = '1'
    # trans contact?
    if rname != rnext:
        flag += 1024 # filter_keys['trans-chromosomic'] = 2**10
    r1r2 = ('{0}\t{1}\t{2}\t{3}\t0\t{4}P\t{6}\t{7}\t{5}\t*\t*\t'
            'TC:i:{8}\t'
            'S1:i:{9}\tS2:i:{10}\n'
            '{0}\t{1}\t{6}\t{7}\t0\t{5}S\t{2}\t{3}\t{4}\t*\t*\t'
            'TC:i:{8}\t'
            'S2:i:{9}\tS1:i:{10}\n').format(
                qname,               # 0
                flag,                # 1
                rname,               # 2
                pos,                 # 3
                l1,                  # 4
                l2,                  # 5
                rnext,               # 6
                pnext,               # 7
                tc,                  # 8
                s1,                  # 9
                s2)                  # 10
    return r1r2


def _map2sam_long(line, flag):
    """
    translate map + flag into hic-sam (two lines per contact)
    """
    (qname,
     rname, pos, s1, l1, e1, e2,
     rnext, pnext, s2, l2, e3, e4) = line.strip().split('\t')

    # multicontact?
    try:
        tc = qname.split('#')[1].split('/')[1]
    except IndexError:
        tc = '1'
    # trans contact?
    if rname != rnext:
        flag += 1024 # filter_keys['trans-chromosomic'] = 2**10
    r1r2 = ('{0}\t{1}\t{2}\t{3}\t0\t{4}P\t{6}\t{7}\t{5}\t*\t*\t'
            'TC:i:{8}\tS1:i:{13}\tS2:i:{14}\t'
            'E1:i:{9}\tE2:i:{10}\tE3:i:{11}\tE4:i:{12}\n'
            '{0}\t{1}\t{6}\t{7}\t0\t{5}S\t{2}\t{3}\t{4}\t*\t*\t'
            'TC:i:{8}\tS2:i:{14}\tS1:i:{13}\t'
            'E3:i:{11}\tE4:i:{12}\tE1:i:{9}\tE2:i:{10}\n').format(
                qname,               # 0
                flag,                # 1
                rname,               # 2
                pos,                 # 3
                l1,                  # 4
                l2,                  # 5
                rnext,               # 6
                pnext,               # 7
                tc,                  # 8
                e1,                  # 9
                e2,                  # 10
                e3,                  # 11
                e4,                  # 12
                s1,                  # 13
                s2)                  # 14
    return r1r2


def bed2D_to_BAMhic(infile, valid, ncpus, outbam, frmt):
    """
    function adapted from Enrique Vidal <enrique.vidal@crg.eu> scipt to convert
    2D beds into compressed BAM format.

    Gets the *_both_filled_map.tsv contacts from TADbit (and the corresponding
    filter files) and outputs a modified indexed BAM with the following fields:

       - read ID
       - filtering flag (see codes in header)
       - chromosome ID of the first pair of the contact
       - genomic position of the first pair of the contact
       - MAPQ set to 0
       - pseudo CIGAR with sequence length and info about current copy (P: first copy, S: second copy)
       - chromosome ID of the second pair of the contact
       - genomic position of the second pair of the contact
       - mapped length of the second pair of the contact
       - sequence is missing (*)
       - quality is missing (*)
       - TC tag indicating single (1) or multi contact (3 6 ... number being the number of times a given sequenced fragment is involved in a pairwise contact)
       - S1 and S2 tags are the strand orientation of the left and right read-end

    Each pair of contacts produces two lines in the output BAM
    """
    # define filter codes
    filter_keys = OrderedDict()
    for k in MASKED:
        filter_keys[MASKED[k]['name'].replace(' ', '-')] = 2 ** (k - 1)

    output = ''

    # write header
    output += ("\t".join(("@HD" ,"VN:1.5", "SO:queryname")) + '\n')

    fhandler = open(infile)
    line = fhandler.next()
    # chromosome lengths
    pos_fh = 0

    while line.startswith('#'):
        (_, _, cr, ln) = line.replace("\t", " ").strip().split(" ")
        output += ("\t".join(("@SQ", "SN:" + cr, "LN:" + ln)) + '\n')
        pos_fh += len(line)
        line = fhandler.next()

    # filter codes
    for i in filter_keys:
        output += ("\t".join(("@CO", "filter:" + i, "flag:" + str(filter_keys[i]))) + '\n')

    # tags
    output += ("\t".join(("@CO" ,"TC:i", "Number of time a sequenced fragment is involved in a pairwise contact\n")))
    output += ("\t".join(("@CO" ,("Each read is duplicated: once starting with the "
                                  "left read-end, once with the right read-end\n"))))
    output += ("\t".join(("@CO" , (" the order of RE sites and strands changes consequently "
                                   "depending on which read-end comes first ("
                                   "when right end is first: E3 E4 E1 E2)\n"))))
    output += ("\t".join(("@CO" ,(" CIGAR code contains the length of the "
                                  "1st read-end mapped and 'P' or 'S' "
                                  "if the copy is the first or the second\n"))))
    output += ("\t".join(("@CO" ,"E1:i", "Position of the left RE site of 1st read-end\n")))
    output += ("\t".join(("@CO" ,"E2:i", "Position of the right RE site of 1st read-end\n")))
    output += ("\t".join(("@CO" ,"E3:i", "Position of the left RE site of 2nd read-end\n")))
    output += ("\t".join(("@CO" ,"E4:i", "Position of the right RE site of 2nd read-end\n")))
    output += ("\t".join(("@CO" ,"S1:i", "Strand of the 1st read-end (1: positive, 0: negative)")))
    output += ("\t".join(("@CO" ,"S2:i", "Strand of the 2nd read-end  (1: positive, 0: negative)")))

    # open and init filter files
    if not valid:
        filter_line, filter_handler = get_filters(infile)
    fhandler.seek(pos_fh)
    proc = Popen('samtools view -Shb -@ %d - | samtools sort -@ %d - %s' % (
        ncpus, ncpus, outbam),
                 shell=True, stdin=PIPE)
    proc.stdin.write(output)
    if frmt == 'mid':
        map2sam = _map2sam_mid
    elif frmt == 'long':
        map2sam = _map2sam_long
    else:
        map2sam = _map2sam_short

    if valid:
        for line in fhandler:
            flag = 0
            # get output in sam format
            proc.stdin.write(map2sam(line, flag))
    else:
        for line in fhandler:
            flag = 0
            # check if read matches any filter
            rid = line.split("\t")[0]
            for i in filter_line:
                if filter_line[i] == rid:
                    flag += filter_keys[i]
                    try:
                        filter_line[i] = filter_handler[i].next().strip()
                    except StopIteration:
                        pass
            # get output in sam format
            proc.stdin.write(map2sam(line, flag))
    proc.stdin.close()
    proc.wait()

    # Index BAM
    _ = Popen('samtools index %s.bam' % (outbam), shell=True).communicate()

    # close file handlers
    fhandler.close()
    if not valid:
        for i in filter_handler:
            filter_handler[i].close()


def get_filters(infile):
    """
    get all filters
    """
    basename = os.path.basename(infile)
    dirname = os.path.dirname(infile)

    filter_files = {}
    stderr.write('Using filter files:\n')
    for fname in os.listdir(dirname):
        if fname.startswith(basename + "_"):
            key = fname.replace(basename + "_", "").replace(".tsv", "")
            filter_files[key] = dirname + "/" + fname
            stderr.write('   - %-20s %s\n' %(key, fname))
    filter_handler = {}
    filter_line = {}
    for i in filter_files:
        filter_handler[i.replace('_', '-')] = open(filter_files[i])
        try:
            filter_line[i.replace('_', '-')] = filter_handler[i.replace('_', '-')].next().strip()
        except StopIteration:
            filter_line[i.replace('_', '-')] = ""
    return filter_line, filter_handler



def _read_bam_frag(inbam, filter_exclude, sections1, sections2, rand_hash,
                   resolution, tmpdir, region, start, end, half=False):
    bamfile = AlignmentFile(inbam, 'rb')
    refs = bamfile.references
    bam_start = start - 2
    bam_start = max(0, bam_start)
    try:
        dico = {}
        for r in bamfile.fetch(region=region,
                               start=bam_start, end=end,  # coords starts at 0
                               multiple_iterators=True):
            if r.flag & filter_exclude:
                continue
            crm1 = r.reference_name
            pos1 = r.reference_start + 1
            crm2 = refs[r.mrnm]
            pos2 = r.mpos + 1
            try:
                pos1 = sections1[(crm1, pos1 / resolution)]
                pos2 = sections2[(crm2, pos2 / resolution)]
            except KeyError:
                continue  # not in the subset matrix we want
            try:
                dico[(pos1, pos2)] += 1
            except KeyError:
                dico[(pos1, pos2)] = 1
        if half:
            for i, j in dico:
                if i < j:
                    del dico[(i,j)]
        out = open(os.path.join(tmpdir, '_tmp_%s' % (rand_hash),
                                '%s:%d-%d.pickle' % (region, start, end)), 'w')
        dump(dico, out)
        out.close()
    except Exception, e:
        exc_type, exc_obj, exc_tb = exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        print e
        print(exc_type, fname, exc_tb.tb_lineno)


def read_bam(inbam, filter_exclude, resolution, ncpus=8,
             region1=None, start1=None, end1=None,
             region2=None, start2=None, end2=None,
             tmpdir='.', verbose=True):

    bamfile = AlignmentFile(inbam, 'rb')
    sections = OrderedDict(zip(bamfile.references,
                               [x / resolution + 1 for x in bamfile.lengths]))
    total = 0
    section_pos = dict()
    for crm in sections:
        section_pos[crm] = (total, total + sections[crm])
        total += sections[crm] + 1
    bins = []
    for crm in sections:
        len_crm = sections[crm]
        bins.extend([(crm, i) for i in xrange(len_crm + 1)])
    if len(bins) == 0:
        raise Exception('ERROR: Chromosome %s smaller than bin size\n' % (crm))
    start_bin1 = 0
    end_bin1   = len(bins) + 1
    if region1:
        if not region1 in section_pos:
            raise Exception('ERROR: chromosome %s not found' % region1)
        regions = [region1]
    else:
        regions = bamfile.references
        total = len(bins)
        if start1 or end1:
            raise Exception('ERROR: Cannot use start/end1 without region')

    if start1 is not None:
        start_bin1 = section_pos[region1][0] + start1 / resolution
    else:
        start_bin1 = section_pos[region1][0]
        start1 = 0
    if end1 is not None:
        end_bin1 = section_pos[region1][0] + end1 / resolution
    else:
        end_bin1 = section_pos[region1][1]
        end1 = sections[region1] * resolution

    # define chunks, using at most 100 sub-divisions of region1
    total = end_bin1 - start_bin1 + 1
    regs  = []
    begs  = []
    ends  = []
    njobs = min(total, 100) + 1
    nbins = total / njobs + 1
    for i in xrange(start_bin1, end_bin1, nbins):
        if i + nbins > end_bin1:  # make sure that we stop at the right place
            nbins = end_bin1 - i
        try:
            (crm1, beg1), (crm2, fin2) = bins[i], bins[i + nbins - 1]
        except IndexError:
            (crm1, beg1), (crm2, fin2) = bins[i], bins[-1]
        if crm1 != crm2:
            fin1 = sections[crm1]
            beg2 = 0
            regs.append(crm1)
            regs.append(crm2)
            begs.append(beg1 * resolution)
            begs.append(beg2 * resolution)
            ends.append(fin1 * resolution + resolution)  # last nt included
            ends.append(fin2 * resolution + resolution - 1)  # last nt not included (overlap with next window)
        else:
            regs.append(crm1)
            begs.append(beg1 * resolution)
            ends.append(fin2 * resolution + resolution - 1)
    ends[-1] += 1  # last nucleotide included

    # reduce dictionaries
    bins = []
    for crm in regions:
        beg_crm = section_pos[crm][0]
        if len(regions) == 1:
            start = start_bin1 - beg_crm
            end   = end_bin1   - beg_crm
        else:
            start = 0
            end   = section_pos[crm][1] - section_pos[crm][0] + 1
        bins.extend([(crm, i) for i in xrange(start, end)])
    bins_dict1 = dict([(j, i) for i, j in enumerate(bins)])
    if region2:
        if not region2 in section_pos:
            raise Exception('ERROR: chromosome %s not found' % region2)
        bins = []
        beg_crm = section_pos[region2][0]
        if start2 is not None:
            start_bin2 = section_pos[region2][0] + start2 / resolution
        else:
            start_bin2 = section_pos[region2][0]
            start2 = 0
        if end2 is not None:
            end_bin2   = section_pos[region2][0] + end2   / resolution
        else:
            end_bin2   = section_pos[region2][1]
            end2       = sections[region2] * resolution
        start = start_bin2 - beg_crm
        end   = end_bin2   - beg_crm
        bins = [(region2, i) for i in xrange(start, end)]
        bins_dict2 = dict([(j, i) for i, j in enumerate(bins)])
    else:
        start_bin2 = start_bin1
        end_bin2 = end_bin1
        bins_dict2 = bins_dict1
    pool = mu.Pool(ncpus)
    # create random hash associated to the run:
    rand_hash = "%016x" % getrandbits(64)

    ## RUN!
    if verbose:
        printime('\n  - Parsing BAM (%d chunks)' % (len(regs)))
    mkdir(os.path.join(tmpdir, '_tmp_%s' % (rand_hash)))
    procs = []
    for i, (region, b, e) in enumerate(zip(regs, begs, ends)):
        if ncpus == 1:
            _read_bam_frag(inbam, filter_exclude,
                           bins_dict1, bins_dict2, rand_hash,
                           resolution, tmpdir, region, b, e,)
        else:
            procs.append(pool.apply_async(
                _read_bam_frag, args=(inbam, filter_exclude,
                                      bins_dict1, bins_dict2, rand_hash,
                                      resolution, tmpdir, region, b, e,)))
    pool.close()
    if verbose:
        print_progress(procs)
    pool.join()
    bin_coords = start_bin1, end_bin1, start_bin2, end_bin2
    chunks = regs, begs, ends
    return regions, rand_hash, bin_coords, chunks


def _iter_matrix_frags(chunks, bads1, bads2, tmpdir, rand_hash,
                       verbose=True):
    if verbose:
        stdout.write('     ')
    countbin = 0
    for countbin, (region, start, end) in enumerate(zip(*chunks)):
        if verbose:
            if not countbin % 10 and countbin:
                stdout.write(' ')
            if not countbin % 50 and countbin:
                stdout.write(' %9s\n     ' % ('%s/%s' % (countbin , len(chunks[0]))))
            stdout.write('.')
            stdout.flush()

        fname = os.path.join(tmpdir, '_tmp_%s' % (rand_hash),
                             '%s:%d-%d.pickle' % (region, start, end))
        dico = load(open(fname))
        for (j, k), v in dico.iteritems():
            if j in bads1 or k in bads2:
                continue
            yield j, k, v
    if verbose:
        print '%s %9s\n' % (' ' * (54 - (countbin % 50) - (countbin % 50) / 10),
                            '%s/%s' % (len(chunks[0]),len(chunks[0])))


def get_biases_region(biases, bin_coords):
    """
    Retrieve biases, decay, and bad bins from a dictionary, and re-index it
    according to a region of interest.
    """
    start_bin1, end_bin1, start_bin2, end_bin2 = bin_coords
    # load decay
    if isinstance(biases, str):
        biases = load(open(biases))
    decay = biases.get('decay' , {})
    # load biases and bad columns
    bias1  = dict((k - start_bin1, v)
                  for k, v in biases.get('biases', {}).iteritems()
                  if start_bin2 <= k <= end_bin1)
    bads1  = dict((k - start_bin1, v)
                  for k, v in biases.get('badcol', {}).iteritems()
                  if start_bin1 <= k <= end_bin1)
    if start_bin1 != start_bin2:
        bias2  = dict((k - start_bin2, v)
                      for k, v in biases.get('biases', {}).iteritems()
                      if start_bin2 <= k <= end_bin2)
        bads2  = dict((k - start_bin2, v)
                      for k, v in biases.get('badcol', {}).iteritems()
                      if start_bin2 <= k <= end_bin2)
    else:
        bias2 = bias1
        bads2 = bads1
    return bias1, bias2, decay, bads1, bads2


def get_matrix(inbam, resolution, biases,
               filter_exclude=(1, 2, 3, 4, 6, 7, 8, 9, 10),
               region1=None, start1=None, end1=None,
               region2=None, start2=None, end2=None,
               tmpdir='.', normalization='raw', ncpus=8, verbose=False):

    if not isinstance(filter_exclude, int):
        filter_exclude = filters_to_bin(filter_exclude)

    _, rand_hash, bin_coords, chunks = read_bam(
        inbam, filter_exclude, resolution, ncpus=ncpus,
        region1=region1, start1=start1, end1=end1,
        region2=region2, start2=start2, end2=end2,
        tmpdir=tmpdir, verbose=verbose)

    bias1, bias2, decay, bads1, bads2 = get_biases_region(biases, bin_coords)

    start_bin1, start_bin2 = bin_coords[::2]

    if verbose:
        printime('  - Getting matrices')

    def transform_value_raw(a, b, c):
        return c
    def transform_value_norm(a, b, c):
        return c / bias1[a] / bias2[b]
    def transform_value_decay(a, b, c):
        return c / bias1[a] / bias2[b] / decay[abs(a-b)]
    def transform_value_decay_2reg(a, b, c):
        return c / bias1[a] / bias2[b] / decay[abs((a + start_bin1) - (b + start_bin2))]

    if normalization == 'raw':
        transform_value = transform_value_raw
    if normalization == 'norm':
        transform_value = transform_value_norm
    if normalization == 'decay':
        if start_bin1 == start_bin2:
            transform_value = transform_value_decay
        else:
            transform_value = transform_value_decay_2reg

    dico = {}
    # pull all sub-matrices and write full matrix
    for i, j, v in _iter_matrix_frags(chunks, bads1, bads2,
                                      tmpdir, rand_hash, verbose=verbose):
        dico[(i, j)] = transform_value(i, j, v)
    if  verbose:
        printime('\nDone.')
    return dico


def write_matrix(inbam, resolution, biases, outdir,
                 filter_exclude=(1, 2, 3, 4, 6, 7, 8, 9, 10),
                 normalizations=('decay',),
                 region1=None, start1=None, end1=None,
                 region2=None, start2=None, end2=None,
                 tmpdir='.', append_to_tar=None, ncpus=8, verbose=True):

    if start1 is not None and end1:
        if end1 - start1 < resolution:
            raise Exception('ERROR: region1 should be at least as big as resolution')
    if start2 is not None and end2:
        if end2 - start2 < resolution:
            raise Exception('ERROR: region2 should be at least as big as resolution')

    if not isinstance(filter_exclude, int):
        filter_exclude = filters_to_bin(filter_exclude)

    regions, rand_hash, bin_coords, chunks = read_bam(
        inbam, filter_exclude, resolution, ncpus=ncpus,
        region1=region1, start1=start1, end1=end1,
        region2=region2, start2=start2, end2=end2,
        tmpdir=tmpdir, verbose=verbose)

    bias1, bias2, decay, bads1, bads2 = get_biases_region(biases, bin_coords)

    start_bin1, start_bin2 = bin_coords[::2]
    if verbose:
        printime('  - Writing matrices')
    # define output file name
    if len(regions) == 1:
        if region2:
            name = '%s:%d-%d_%s:%d-%d' % (region1, start1 / resolution, end1 / resolution,
                                          region2, start2 / resolution, end2 / resolution)
        elif start1 is not None:
            name = '%s:%d-%d' % (region1, start1 / resolution, end1 / resolution)
        else:
            name = '%s' % (region1)
    else:
        name = 'full'

    # prepare file header
    outfiles = []
    if 'raw' in normalizations:
        fnam = 'raw_%s_%s.abc' % (name,
                                  nicer(resolution).replace(' ', ''))
        if append_to_tar:
            out_raw = StringIO()
            outfiles.append((out_raw, fnam))
        else:
            out_raw = open(os.path.join(outdir, fnam), 'w')
            outfiles.append((os.path.join(outdir, fnam), fnam))
        out_raw.write('# %s resolution:%d\n' % (name, resolution))
        if region2:
            out_raw.write('# BADROWS %s\n' % (','.join([str(b) for b in bads1])))
            out_raw.write('# BADCOLS %s\n' % (','.join([str(b) for b in bads2])))
        else:
            out_raw.write('# BADS %s\n' % (','.join([str(b) for b in bads1])))

    # write file header
    if 'norm' in normalizations:
        fnam = 'nrm_%s_%s.abc' % (name,
                                  nicer(resolution).replace(' ', ''))
        if append_to_tar:
            out_nrm = StringIO()
            outfiles.append((out_nrm, fnam))
        else:
            out_nrm = open(os.path.join(outdir, fnam), 'w')
            outfiles.append((os.path.join(outdir, fnam), fnam))
        out_nrm.write('# %s resolution:%d\n' % (name, resolution))
        if region2:
            out_nrm.write('# BADROWS %s\n' % (','.join([str(b) for b in bads1])))
            out_nrm.write('# BADCOLS %s\n' % (','.join([str(b) for b in bads2])))
        else:
            out_nrm.write('# BADS %s\n' % (','.join([str(b) for b in bads1])))
    if 'decay' in normalizations:
        fnam = 'dec_%s_%s.abc' % (name,
                                  nicer(resolution).replace(' ', ''))
        if append_to_tar:
            out_dec = StringIO()
            outfiles.append((out_dec, fnam))
        else:
            out_dec = open(os.path.join(outdir, fnam), 'w')
            outfiles.append((os.path.join(outdir, fnam), fnam))
        out_dec.write('# %s resolution:%d\n' % (
            name, resolution))
        if region2:
            out_dec.write('# BADROWS %s\n' % (','.join([str(b) for b in bads1])))
            out_dec.write('# BADCOLS %s\n' % (','.join([str(b) for b in bads2])))
        else:
            out_dec.write('# BADS %s\n' % (','.join([str(b) for b in bads1])))

    # functions to write lines of pairwise interactions
    def write_raw(func=None):
        def writer2(a, b, c):
            func(a, b, c)
            out_raw.write('%d\t%d\t%d\n' % (a, b, c))
        def writer(a, b, c):
            out_raw.write('%d\t%d\t%d\n' % (a, b, c))
        return writer2 if func else writer

    def write_bias(func=None):
        def writer2(a, b, c):
            func(a, b, c)
            out_nrm.write('%d\t%d\t%f\n' % (a, b, c / bias1[a] / bias2[b]))
        def writer(a, b, c):
            out_nrm.write('%d\t%d\t%f\n' % (a, b, c / bias1[a] / bias2[b]))
        return writer2 if func else writer

    def write_expc(func=None):
        def writer2(a, b, c):
            func(a, b, c)
            out_dec.write('%d\t%d\t%f\n' % (
                a, b, c / bias1[a] / bias2[b] / decay[abs(a-b)]))
        def writer(a, b, c):
            out_dec.write('%d\t%d\t%f\n' % (
                a, b, c / bias1[a] / bias2[b] / decay[abs(a-b)]))
        return writer2 if func else writer

    def write_expc_2reg(func=None):
        def writer2(a, b, c):
            func(a, b, c)
            out_dec.write('%d\t%d\t%f\n' % (
                a, b, c / bias1[a] / bias2[b] / decay[abs((a + start_bin1) - (b + start_bin2))]))
        def writer(a, b, c):
            out_dec.write('%d\t%d\t%f\n' % (
                a, b, c / bias1[a] / bias2[b] / decay[abs((a + start_bin1) - (b + start_bin2))]))
        return writer2 if func else writer

    def write_expc_err(func=None):
        def writer2(a, b, c):
            func(a, b, c)
            try:
                out_dec.write('%d\t%d\t%f\n' % (
                    a, b, c / bias1[a] / bias2[b] / decay[abs(a-b)]))
            except KeyError:  # different chromosomes
                out_dec.write('%d\t%d\t%s\n' % (a, b, 'nan'))
        def writer(a, b, c):
            try:
                out_dec.write('%d\t%d\t%f\n' % (
                    a, b, c / bias1[a] / bias2[b] / decay[abs(a-b)]))
            except KeyError:  # different chromosomes
                out_dec.write('%d\t%d\t%s\n' % (a, b, 'nan'))
        return writer2 if func else writer

    write = None
    if 'raw'   in normalizations:
        write = write_raw(write)
    if 'norm'  in normalizations:
        write = write_bias(write)
    if 'decay' in normalizations:
        if len(regions) == 1:
            if region2:
                write = write_expc_2reg(write)
            else:
                write = write_expc(write)
        else:
            write = write_expc_err(write)

    # pull all sub-matrices and write full matrix
    for j, k, v in _iter_matrix_frags(chunks, bads1, bads2,
                                      tmpdir, rand_hash):
        write(j, k, v)

    if append_to_tar:
        lock = LockFile(append_to_tar)
        with lock:
            archive = taropen(append_to_tar, "a:")
            for fobj, fnam in outfiles:
                fobj.seek(0)
                info = archive.tarinfo(name=fnam)
                info.size=len(fobj.buf)
                archive.addfile(tarinfo=info, fileobj=fobj)
            archive.close()
    else:
        if 'raw' in normalizations:
            out_raw.close()
        if 'norm' in normalizations:
            out_nrm.close()
        if 'decay' in normalizations:
            out_dec.close()

    # this is the last thing we do in case something goes wrong
    os.system('rm -rf %s' % (os.path.join(tmpdir, '_tmp_%s' % (rand_hash))))

    if  verbose:
        printime('\nDone.')