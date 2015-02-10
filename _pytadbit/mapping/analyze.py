"""
18 Nov 2014


"""
from pytadbit.utils.extraviews import tadbit_savefig
from pytadbit.utils.tadmaths import nozero_log_matrix as nozero_log
from warnings import warn
from collections import OrderedDict
import numpy as np
from pytadbit.parsers.hic_parser import load_hic_data_from_reads
from scipy.stats import norm as sc_norm, skew, kurtosis
import os

try:
    from matplotlib import pyplot as plt
except ImportError:
    warn('matplotlib not found\n')

def hic_map(data, resolution, biases=None, masked=None, by_chrom=False,
            savefig=None, show=False, savedata=None, focus=None, clim=None,
            cmap='Reds'):
    """
    function to retrieve data from HiC-data object. Data can be stored as
    a square matrix, or drawn using matplotlib

    :param data: can be either a path to a file with pre-processed reads
       (filtered or not), or a Hi-C-data object
    :param resolution: at which to bin the data (try having a dense matrix
       with < 10% of cells with zero interaction counts).
    :param biases: a list of biases, one per column, each cell (i, j) will be
       divided by the product: BixBj
    :param masked: a list of columns to be removed. Usually because to few
       interactions
    :param False by_chrom: data can be stored in a partitioned way. This
       parameter can take the values of:
        * 'intra': one output per each chromosome will be created
        * 'inter': one output per each possible pair of chromosome will be
           created
        * 'all'  : both of the above outputs
    :param None savefig: path where to store the output images. Note that, if
       the by_chrom option is used, then savefig will be the name of the
       directory containing the output files.
    :param None savedata: path where to store the output matrices. Note that, if
       the by_chrom option is used, then savefig will be the name of the
       directory containing the output files.
    :param None focus: can be either two number (i.e.: (1, 100)) specifying the
       start and end position of the sub-matrix to display (start and end, along
       the diagonal of the original matrix); or directly a chromosome name; or
       two chromosome names (i.e.: focus=('chr2, chrX')), in order to store the
       data corresponding to inter chromosomal interactions between these two
       chromosomes
    :param None clim: cutoff for the upper and lower bound in the coloring scale
       of the heatmap
    :param Reds cmap: color map to be used for the heatmap
    """
    if isinstance(data, str):
        data = load_hic_data_from_reads(data, resolution=resolution)
    hic_data = data
    hic_data.bias = biases
    hic_data.bads = masked
    # save and draw the data
    if by_chrom:
        if focus:
            raise Exception('Incompatible options focus and by_chrom\n')
        os.system('mkdir -p ' + savedata)
        if not clim:
            clim = (np.log2(min(hic_data.values())),
                    np.log2(max(hic_data.values())))
        for i, crm1 in enumerate(hic_data.chromosomes):
            for crm2 in hic_data.chromosomes.keys()[i:]:
                if by_chrom == 'intra' and crm1 != crm2:
                    continue
                if by_chrom == 'inter' and crm1 == crm2:
                    continue
                subdata = hic_data.get_matrix(focus=(crm1, crm2))
                if savedata:
                    out = open('%s/%s.mat' % (
                        savedata, '_'.join(set((crm1, crm2)))), 'w')
                    out.write('\n'.join(['\t'.join([str(i) for i in d])
                                         for d in subdata]) + '\n')
                    out.close()
                if show or savefig:
                    draw_map(subdata, 
                             OrderedDict([(k, hic_data.chromosomes[k])
                                          for k in hic_data.chromosomes.keys()
                                          if k in [crm1, crm2]]),
                             hic_data.section_pos,
                             '%s/%s.pdf' % (savefig,
                                            '_'.join(set((crm1, crm2)))),
                             show, one=True, clim=clim, cmap=cmap)
    else:
        if savedata:
            if not focus:
                out = open(savedata, 'w')
                for i in xrange(len(hic_data)):
                    out.write('\t'.join([str(hic_data[i,j])
                                         for j in xrange(len(hic_data))]) + '\n')
                out.close()
            else:
                out = open(savedata, 'w')
                out.write('\n'.join(
                    ['\t'.join([str(i) for i in line])
                     for line in hic_data.get_matrix(focus=focus)]) + '\n')
                out.close()
        if show or savefig:
            subdata = nozero_log(hic_data.get_matrix(focus=focus), np.log2)
            if masked:
                for i in xrange(len(subdata)):
                    if i in masked:
                        subdata[i] = [float('nan')
                                      for j in xrange(len(subdata))]
                    for j in xrange(len(subdata)):
                        if j in masked:
                            subdata[i][j] = float('nan') 
            draw_map(subdata,
                     {} if focus else hic_data.chromosomes,
                     hic_data.section_pos, savefig, show,
                     one = True if focus else False,
                     clim=clim, cmap=cmap)


def draw_map(data, genome_seq, cumcs, savefig, show, one=False,
             clim=None, cmap='Reds'):
    fig = plt.figure(figsize=(10.,9.1))
    axe = fig.add_subplot(111)
    cmap = plt.get_cmap(cmap)
    cmap.set_bad('darkgrey', 1)
    axe.imshow(data, interpolation='none',
               cmap=cmap, vmin=clim[0] if clim else None,
               vmax=clim[1] if clim else None)
    size = len(data)
    data = [i for d in data for i in d if not np.isnan(i)]
    fig.subplots_adjust(top=.8, left=0.35)
    gradient = np.linspace(np.nanmin(data),
                           np.nanmax(data), size)
    gradient = np.vstack((gradient, gradient))
    axe2 = fig.add_axes([0.1, 0.78, 0.2, 0.1])
    h  = axe2.hist(data, color='lightgrey',
                   bins=20, histtype='step', normed=True)
    _  = axe2.imshow(gradient, aspect='auto', cmap=cmap,
                     extent=(np.nanmin(data), np.nanmax(data) , 0, max(h[0])))
    for crm in genome_seq:
        axe.vlines(cumcs[crm][0]-.5, cumcs[crm][0]-.5, cumcs[crm][1]-.5, color='k',
                   linestyle=':')
        axe.vlines(cumcs[crm][1]-.5, cumcs[crm][0]-.5, cumcs[crm][1]-.5, color='k',
                   linestyle=':')
        axe.hlines(cumcs[crm][1]-.5, cumcs[crm][0]-.5, cumcs[crm][1]-.5, color='k',
                   linestyle=':')
        axe.hlines(cumcs[crm][0]-.5, cumcs[crm][0]-.5, cumcs[crm][1]-.5, color='k',
                   linestyle=':')
    axe2.set_xlim((np.nanmin(data), np.nanmax(data)))
    axe2.set_ylim((0, max(h[0])))
    axe.set_xlim ((-0.5, size - .5))
    axe.set_ylim ((-0.5, size - .5))
    axe2.set_xlabel('log interaction count', size='small')
    normfit = sc_norm.pdf(data, np.mean(data), np.std(data))
    axe2.plot(data, normfit, 'g.', markersize=1, alpha=.1)
    axe2.set_title('skew: %.3f, kurtosis: %.3f' % (skew(data),
                                                   kurtosis(data)),
                   size='small')
    if not one:
        vals = [0]
        keys = ['']
        for crm in genome_seq:
            vals.append(cumcs[crm][0])
            keys.append(crm)
        vals.append(cumcs[crm][1])
        axe.set_yticks(vals)
        axe.set_yticklabels('')
        axe.set_yticks([float(vals[i]+vals[i+1])/2 for i in xrange(len(vals) - 1)], minor=True)
        axe.set_yticklabels(keys, minor=True)
        for t in axe.yaxis.get_minor_ticks():
            t.tick1On = False
            t.tick2On = False 
    if savefig:
        tadbit_savefig(savefig)
    elif show:
        plt.show()
    plt.close('all')

def plot_distance_vs_interactions(fnam, min_diff=100, max_diff=1000000,
                                  resolution=100, axe=None, savefig=None):
    """
    :param fnam: input file name
    :param 100 min_diff: lower limit kn genomic distance (usually equal to read
       length)
    :param 1000000 max_diff: upper limit in genomic distance to look for
    :param 100 resolution: group reads that are closer than this resolution
       parameter
    :param None axe: a matplotlib.axes.Axes object to define the plot
       appearance
    :param None savefig: path to a file where to save the image generated;
       if None, the image will be shown using matplotlib GUI (the extension
       of the file name will determine the desired format).
    
    """
    dist_intr = {}
    fhandler = open(fnam)
    line = fhandler.next()
    while line.startswith('#'):
        line = fhandler.next()
    try:
        while True:
            _, cr1, ps1, _, _, _, _, cr2, ps2, _ = line.rsplit('\t', 9)
            if cr1 != cr2:
                line = fhandler.next()
                continue
            diff = resolution * (abs(int(ps1) - int(ps2)) / resolution)
            if max_diff > diff > min_diff:
                dist_intr.setdefault(diff, 0)
                dist_intr[diff] += 1
            line = fhandler.next()
    except StopIteration:
        pass
    fhandler.close()
            
    for k in dist_intr.keys()[:]:
        if dist_intr[k] <= 2:
            del(dist_intr[k])
                    
    if not axe:
        fig=plt.figure()
        _ = fig.add_subplot(111)
        
    x, y = zip(*sorted(dist_intr.items(), key=lambda x:x[0]))
    plt.plot(x, y, 'k.')
    # sigma = 10
    # p_x = gaussian_filter1d(x, sigma)
    # p_y = gaussian_filter1d(y, sigma)
    # plot line of best fit
    # plt.plot(p_x, p_y,color= 'darkgreen', lw=2, label='Gaussian fit')
    plt.yscale('log')
    plt.xscale('log')
    plt.xlabel('Log genomic distance (binned by %d bp)' % resolution)
    plt.ylabel('Log interaction count')
    # plt.legend()
    if savefig:
        tadbit_savefig(savefig)
    elif not axe:
        plt.show()


def plot_iterative_mapping(fnam1, fnam2, total_reads=None, axe=None, savefig=None):
    """
    :param fnam: input file name
    :param total_reads: total number of reads in the initial FASTQ file
    :param None axe: a matplotlib.axes.Axes object to define the plot
       appearance
    :param None savefig: path to a file where to save the image generated;
       if None, the image will be shown using matplotlib GUI (the extension
       of the file name will determine the desired format).
    :returns: a dictionary with the number of reads per mapped length
    """
    count_by_len = {}
    total_reads = total_reads or 1
    if not axe:
        fig=plt.figure()
        _ = fig.add_subplot(111)
    colors = ['olive', 'darkcyan']
    for i, fnam in enumerate([fnam1, fnam2]):
        fhandler = open(fnam)
        line = fhandler.next()
        while line.startswith('#'):
            line = fhandler.next()
        try:
            count_by_len[i] = {}
            while True:
                _, length, _, _ = line.rsplit('\t', 3)
                try:
                    count_by_len[i][int(length)] += 1
                except KeyError:
                    count_by_len[i][int(length)] = 1
                line = fhandler.next()
        except StopIteration:
            pass
        fhandler.close()
        lengths = sorted(count_by_len[i].keys())
        for k in lengths[::-1]:
            count_by_len[i][k] += sum([count_by_len[i][j]
                                       for j in lengths if j < k])
        plt.plot(lengths, [float(count_by_len[i][l]) / total_reads
                           for l in lengths],
                 label='read' + str(i + 1), linewidth=2, color=colors[i])
    plt.xlabel('read length (bp)')
    if total_reads != 1:
        plt.ylabel('Proportion of mapped reads')
    else:
        plt.ylabel('Number of mapped reads')
    plt.legend(loc=4)
    if savefig:
        tadbit_savefig(savefig)
    elif not axe:
        plt.show()
    return count_by_len


def plot_genomic_distribution(fnam, first_read=True, resolution=10000,
                              axe=None, ylim=None, savefig=None):
    """
    :param fnam: input file name
    :param True first_read: map first read.
    :param 100 resolution: group reads that are closer than this resolution
       parameter
    :param None axe: a matplotlib.axes.Axes object to define the plot
       appearance
    :param None savefig: path to a file where to save the image generated;
       if None, the image will be shown using matplotlib GUI (the extension
       of the file name will determine the desired format).
    
    """

    distr = {}
    idx1, idx2 = (1, 3) if first_read else (7, 9)
    genome_seq = OrderedDict()
    fhandler = open(fnam)
    line = fhandler.next()
    while line.startswith('#'):
        if line.startswith('# CRM '):
            crm, clen = line[6:].split()
            genome_seq[crm] = int(clen)
        line = fhandler.next()
    try:
        while True:
            crm, pos = line.strip().split('\t')[idx1:idx2]
            pos = int(pos) / resolution
            try:
                distr[crm][pos] += 1
            except KeyError:
                try:
                    distr[crm][pos] = 1
                except KeyError:
                    distr[crm] = {pos: 1}
            line = fhandler.next()
    except StopIteration:
        pass
    fhandler.close()
    if not axe:
        _ = plt.figure(figsize=(15, 3 * len(distr.keys())))

    max_y = max([max(distr[c].values()) for c in distr])
    max_x = max([len(distr[c].values()) for c in distr])
    for i, crm in enumerate(genome_seq if genome_seq else distr):
        plt.subplot(len(distr.keys()), 1, i + 1)
        plt.plot(range(max(distr[crm])),
                 [distr[crm].get(j, 0) for j in xrange(max(distr[crm]))],
                 color='red', lw=1.5, alpha=0.7)
        if ylim:
            plt.vlines(genome_seq[crm] / resolution, ylim[0], ylim[1])
        else:
            plt.vlines(genome_seq[crm] / resolution, 0, max_y)
        plt.xlim((0, max_x))
        plt.ylim(ylim or (0, max_y))
        plt.title(crm)

    if savefig:
        tadbit_savefig(savefig)
    elif not axe:
        plt.show()

