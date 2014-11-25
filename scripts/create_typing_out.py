#!/usr/bin/env python

# read in genbank file, print out coordinates & strand of features
from argparse import (ArgumentParser, FileType)
from Bio import SeqIO
from Bio import SeqFeature
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.Alphabet import generic_dna
from Bio.Blast.Applications import NcbiblastnCommandline
from operator import itemgetter
import os, sys, re, collections, operator
import numpy as np
from collections import OrderedDict
from compiled_table import get_flanking_genes, get_other_gene, get_qualifiers

def parse_args():

    parser = ArgumentParser(description="create a table of features for ISMapper")
    parser.add_argument('--intersect', type=str, required=True, help='intersection bed file')
    parser.add_argument('--closest', type=str, required=True, help='closest bed file')
    parser.add_argument('--left_bed', type=str, required=True, help='merged bed file for left end (5)')
    parser.add_argument('--right_bed', type=str, required=True, help='merged bed file for right end (3)')
    parser.add_argument('--left_unpaired', type=str, required=True, help='closest bed file where left end is full coverage')
    parser.add_argument('--right_unpaired', type=str, required=True, help='closest bed file where right end is full coverage')
    parser.add_argument('--ref', type=str, required=True, help='reference genbank file to find flanking genes of regions')
    parser.add_argument('--seq', type=str, required=True, help='insertion sequence reference in fasta format')
    parser.add_argument('--cds', nargs='+', type=str, required=False, default=['locus_tag', 'gene', 'product'], help='qualifiers to look for in reference genbank for CDS features (default locus_tag gene product)')
    parser.add_argument('--trna', nargs='+', type=str, required=False, default=['locus_tag', 'product'], help='qualifiers to look for in reference genbank for tRNA features (default locus_tag product)')
    parser.add_argument('--rrna', nargs='+', type=str, required=False, default=['locus_tag', 'product'], help='qualifiers to look for in reference genbank for rRNA features (default locus_tag product)')
    parser.add_argument('--min_range', type=float, required=False, default=0.5, help='Minimum percent size of the gap to be called a known hit (default 0.5, or 50 percent)')
    parser.add_argument('--max_range', type=float, required=False, default=1.1, help='Maximum percent size of the gap to be called a known hit (default 1.1, or 110 percent)')
    parser.add_argument('--temp', type=str, required=True, help='location of temp folder to place intermediate blast files in')
    parser.add_argument('--output', type=str, required=True, help='name for output file')
    return parser.parse_args()

def insertion_length(insertion):

    sequence = SeqIO.read(insertion, "fasta")
    length = len(sequence.seq)

    return length

def doBlast(blast_input, blast_output, database):
    #perform BLAST
    blastn_cline = NcbiblastnCommandline(query=blast_input, db=database, outfmt="'6 qseqid qlen sacc pident length slen sstart send evalue bitscore qcovs'", out=blast_output)
    stdout, stderr = blastn_cline()

def check_seq_between(gb, insertion, start, end, name, temp):

    genbank = SeqIO.read(gb, 'genbank')
    seq_between = genbank.seq[start:end]
    seq_between = SeqRecord(Seq(str(seq_between), generic_dna), id=name)
    SeqIO.write(seq_between, temp + name + '.fasta', 'fasta')
    doBlast(temp + name + '.fasta', temp + name + '_out.txt', insertion)
    first_result = 0
    with open(temp + name + '_out.txt') as summary:
        for line in summary:
            if first_result == 0:
                info = line.strip().split('\t')
                coverage = float(info[4]) / float(info[5]) * 100
                #coverage = info[-1]
                hit = [info[3], coverage]
                first_result += 1
            #os.system('rm ' + name + '.fasta ' + name + '_out.txt')
            return hit
    #os.system('rm ' + name + '.fasta ' + name + '_out.txt')
    hit = []
    return []

def createFeature(hits, orient):

    x_L = hits[0]
    y_L = hits[1]
    x_R = hits[2]
    y_R = hits[3]
    quals = {}

    left_location = SeqFeature.FeatureLocation(x_L, y_L)
    right_location = SeqFeature.FeatureLocation(x_R, y_R)
    if orient == 'F':
        #then in forward orientation, set colour to be red
        quals['colour'] = '2'
        quals['orientation'] = 'forward'
    elif orient == 'R':
        #then in reverse orientation, set colour to be yellow
        quals['colour'] = '7'
        quals['orientation'] = 'reverse'

    left_feature = SeqFeature.SeqFeature(left_location, type='left_end', qualifiers=quals)
    right_feature = SeqFeature.SeqFeature(right_location, type='right_end', qualifiers=quals)

    return left_feature, right_feature

def novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, ref, cds, trna, rrna, gap, orient, feature_count, region, results, unpaired=False, star=False):
    
    left_feature, right_feature = createFeature([x_L, y_L, x_R, y_R], orient)
    genbank.features.append(left_feature)
    genbank.features.append(right_feature)
    feature_count += 2
    
    gene_left, gene_right = get_flanking_genes(ref, x, y, cds, trna, rrna)
    print gene_left
    print gene_right
    if gene_left[-1] == gene_right[-1]:
        funct_pred = 'Gene interrupted'
    else:
        funct_pred = ''
    if unpaired == False:
        call = 'Novel'
    elif unpaired == True:
        call = 'Novel?'
    if star == True:
        call = 'Novel*'
    
    results['region_' + str(region)] = [orient, str(x), str(y), gap, call, '', '', gene_left[-1][:-1], gene_left[-1][-1], gene_left[1], gene_right[-1][:-1], gene_right[-1][-1], gene_right[1], funct_pred]

def main():

    args = parse_args()

    results = {}
    removed_results = {}
    region = 1
    lines = 0
    header = ["region", "orientation", "x", "y", "gap", "call", "%ID", "%Cov", "left_gene", "left_strand", "left_distance", "right_gene", "right_strand", "right_distance", "functional_prediction"]
    if os.stat(args.intersect)[6] == 0 and os.stat(args.closest)[6] == 0:
        output = open(args.output + '_table.txt', 'w')
        output.write('\t'.join(header) + '\n')
        output.write('No hits found')
        output.close()
        sys.exit()

    genbank = SeqIO.read(args.ref, 'genbank')
    feature_count = 0

    intersect_left = []
    intersect_right = []
    closest_left = []
    closest_right = []

    if os.stat(args.intersect)[6] != 0:
        with open(args.intersect) as bed_merged:
            for line in bed_merged:
                info = line.strip().split('\t')
                intersect_left.append(info[0:3])
                intersect_right.append(info[3:6])
                
                #set up coordinates for checking: L is the left end of the IS (5') and R is the right end of the IS (3')
                #eg x_L and y_L are the x and y coordinates of the bed block that matches to the region which is flanking the left end or 5' of the IS
                x_L = int(info[1])
                y_L = int(info[2])
                x_R = int(info[4])
                y_R = int(info[5])
                
                #check to see if the gap is reasonable
                if int(info[6]) <= 15:
                    if x_L < x_R or y_L < y_R:
                        orient = 'F'
                        x = x_R
                        y = y_L
                    elif x_L > x_R or y_L > y_R:
                        orient = 'R'
                        x = x_L
                        y = y_R
                    else:
                        print 'neither if statement were correct'

                    novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, args.ref, args.cds, args.trna, args.rrna, info[6], orient, feature_count, region, results, unpaired=False)
                    region += 1
                else:
                    removed_results['region_' + str(lines)] = line.strip() + '\tintersect.bed\n'
                lines += 1
    
    is_length = insertion_length(args.seq)
    with open(args.closest) as bed_closest:
        for line in bed_closest:
            info = line.strip().split('\t')
            closest_left.append(info[0:3])
            closest_right.append(info[3:6])
            
            # then there are no closest regions, this is a dud file
            if info[3] == '-1':
                output = open(args.output, 'w')
                output.write('\t'.join(header) + '\n')
                output.write('No hits found')
                output.close()
                sys.exit()

            x_L = int(info[1])
            y_L = int(info[2])
            x_R = int(info[4])
            y_R = int(info[5])
            if x_L < x_R and y_L < y_R:
                orient = 'F'
                x = x_R
                y = y_L
            elif x_L > x_R and y_L > y_R:
                orient = 'R'
                x = x_L
                y = y_R
            #this is an overlap, so will be in the intersect file
            if int(info[6]) == 0:
                pass
            #this is probably a novel hit where there was no overlap detected
            elif int(info[6]) <= 10:
                novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, args.ref, args.cds, args.trna, args.rrna, info[6], orient, feature_count, region, results, unpaired=False)
                region += 1
            #this is probably a known hit, but need to check with BLAST
            elif float(info[6]) / is_length >= args.min_range and float(info[6]) / is_length <= args.max_range:
                if y_L < x_R:
                    start = y_L
                    end = x_R
                    orient = 'F'
                else:
                    start = y_R
                    end = x_L
                    orient = 'R'
                
                left_feature, right_feature = createFeature([x_L, y_L, x_R, y_R], orient)
                genbank.features.append(left_feature)
                genbank.features.append(right_feature)
                feature_count += 2

                seq_results = check_seq_between(args.ref, args.seq, start, end, 'region_' + str(region), args.temp)
                if len(seq_results) != 0 and seq_results[0] >= 80 and seq_results[1] >= 80:
                    #then this is definitely a known site
                    gene_left = get_other_gene(args.ref, min(start, end), "left", args.cds, args.trna, args.rrna)
                    gene_right = get_other_gene(args.ref, max(start, end), "right", args.cds, args.trna, args.rrna)
                    results['region_' + str(region)] = [orient, str(start), str(end), info[6], 'Known', str(seq_results[0]), str('%.2f' % seq_results[1]), gene_left[-1][:-1], gene_left[-1][-1], gene_left[1], gene_right[-1][:-1], gene_right[-1][-1], gene_right[1]]
                else:
                   #then I'm not sure what this is
                   print 'not sure'
                   gene_left, gene_right = get_flanking_genes(args.ref, start, end, args.cds, args.trna, args.rrna)
                   if len(seq_results) !=0:
                       results['region_' + str(region)] = [orient, str(start), str(end), info[6], 'Possible related IS', str(seq_results[0]), str('%.2f' % seq_results[1]), gene_left[-1][:-1], gene_left[-1][-1], gene_left[1], gene_right[-1][:-1], gene_right[-1][-1], gene_right[1]]
                   else:
                        removed_results['region_' + str(region)] = line.strip() + '\tclosest.bed\n'                
                region += 1
            #could possibly be a novel hit but the gap size is too large
            elif float(info[6]) / is_length <= args.min_range and float(info[6]) / is_length < args.max_range:
                novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, args.ref, args.cds, args.trna, args.rrna, info[6], orient, feature_count, region, results, unpaired=False,star=True)
                region +=1
            #this is something else altogether - either the gap is really large or something, place it in removed_results
            else:
                removed_results['region_' + str(region)] = line.strip() + '\tclosest.bed\n'
                region += 1

    #looking for unpaired hits which are not in the merged/closest bed files
    #possibly unpaired due to a repeat on one end of the IS
    line_check = []
    with open(args.left_bed) as left_bed:
        for line in left_bed:
            if line.strip().split('\t') not in intersect_left and line.strip().split('\t') not in closest_left:
                line_check.append(line.strip().split('\t'))
    if len(line_check) != 0:
        with open(args.left_unpaired) as left_unpaired:
            for line in left_unpaired:
                info = line.strip().split('\t')
                #this is an unpaired hit
                if line.strip().split('\t')[0:3] in line_check:
                    #get coordinate info
                    x_L = int(info[1])
                    y_L = int(info[2])
                    x_R = int(info[4])
                    y_R = int(info[5])
                    #get orientation
                    if x_L < x_R and y_L < y_R:
                        orient = 'F'
                        x = x_R
                        y = y_L
                    elif x_L > x_R and y_L > y_R:
                        orient = 'R'
                        x = x_L
                        y = y_R
                    #a novel hit
                    if float(info[6]) <= 10:
                        novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, args.ref, args.cds, args.trna, args.rrna, info[6], orient, feature_count, region, results, unpaired=True)
                        region += 1
                    #a known hit
                    elif float(info[6]) / is_length >= args.min_range and float(info[6]) / is_length <= args.max_range:
                        if y_L < x_R:
                            start = y_L
                            end = x_R
                            orient = 'F'
                        else:
                            start = y_R
                            end = x_L
                            orient = 'R'
                        
                        left_feature, right_feature = createFeature([x_L, y_L, x_R, y_R], orient)
                        genbank.features.append(left_feature)
                        genbank.features.append(right_feature)
                        feature_count += 2

                        seq_results = check_seq_between(args.ref, args.seq, start, end, 'region_' + str(region), args.temp)
                        if len(seq_results) != 0 and seq_results[0] >= 80 and seq_results[1] >= 80:
                            #then this is definitely a known site
                            gene_left = get_other_gene(args.ref, min(start, end), "left", args.cds, args.trna, args.rrna)
                            gene_right = get_other_gene(args.ref, max(start, end), "right", args.cds, args.trna, args.rrna)
                            results['region_' + str(region)] = [orient, str(start), str(end), info[6], 'Known?', str(seq_results[0]), str('%.2f' % seq_results[1]), gene_left[-1][:-1], gene_left[-1][-1], gene_left[1], gene_right[-1][:-1], gene_right[-1][-1], gene_right[1]]
                        else:
                           #then I'm not sure what this is
                           print 'not sure'
                           gene_left, gene_right = get_flanking_genes(args.ref, start, end, args.cds, args.trna, args.rrna)
                           if len(seq_results) !=0:
                               results['region_' + str(region)] = [orient, str(start), str(end), info[6], 'Possible related IS?', str(seq_results[0]), str('%.2f' % seq_results[1]), gene_left[-1][:-1], gene_left[-1][-1], gene_left[1], gene_right[-1][:-1], gene_right[-1][-1], gene_right[1]]
                           else:
                                removed_results['region_' + str(region)] = line.strip() + '\tleft_unpaired.bed\n'                
                        region += 1
                    #could possibly be a novel hit but the gap size is too large
                    elif float(info[6]) / is_length <= args.min_range and float(info[6]) / is_length < args.max_range:

                        novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, args.ref, args.cds, args.trna, args.rrna, info[6], orient, feature_count, region, results, unpaired=True)
                        region +=1
                    #this is something else altogether - either the gap is really large or something, place it in removed_results
                    else:
                        removed_results['region_' + str(region)] = line.strip() + '\tleft_unpaired.bed\n'
                        region += 1
    line_check = []
    with open(args.right_bed) as right_bed:
        for line in right_bed:
            if line.strip().split('\t') not in intersect_right and line.strip().split('\t') not in closest_right:
                line_check.append(line.strip().split('\t'))
    if len(line_check) != 0:
        with open(args.right_unpaired) as right_unpaired:
            for line in right_unpaired:
                info = line.strip().split('\t')
                #this is an unpaired hit
                if line.strip().split('\t')[3:6] in line_check:
                    #get coordinate info
                    x_L = int(info[1])
                    y_L = int(info[2])
                    x_R = int(info[4])
                    y_R = int(info[5])
                    #get orientation
                    if x_L < x_R and y_L < y_R:
                        orient = 'F'
                        x = x_R
                        y = y_L
                    elif x_L > x_R and y_L > y_R:
                        orient = 'R'
                        x = x_L
                        y = y_R
                    #a novel hit
                    if float(info[6]) <= 10:
                        novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, args.ref, args.cds, args.trna, args.rrna, info[6], orient, feature_count, region, results, unpaired=True)
                        region += 1
                    #a known hit
                    elif float(info[6]) / is_length >= args.min_range and float(info[6]) / is_length <= args.max_range:
                        if y_L < x_R:
                            start = y_L
                            end = x_R
                            orient = 'F'
                        else:
                            start = y_R
                            end = x_L
                            orient = 'R'
                        
                        left_feature, right_feature = createFeature([x_L, y_L, x_R, y_R], orient)
                        genbank.features.append(left_feature)
                        genbank.features.append(right_feature)
                        feature_count += 2

                        seq_results = check_seq_between(args.ref, args.seq, start, end, 'region_' + str(region), args.temp)
                        if len(seq_results) != 0 and seq_results[0] >= 80 and seq_results[1] >= 80:
                            #then this is definitely a known site
                            gene_left = get_other_gene(args.ref, min(start, end), "left", args.cds, args.trna, args.rrna)
                            gene_right = get_other_gene(args.ref, max(start, end), "right", args.cds, args.trna, args.rrna)
                            results['region_' + str(region)] = [orient, str(start), str(end), info[6], 'Known?', str(seq_results[0]), str('%.2f' % seq_results[1]), gene_left[-1][:-1], gene_left[-1][-1], gene_left[1], gene_right[-1][:-1], gene_right[-1][-1], gene_right[1]]
                        else:
                           #then I'm not sure what this is
                           print 'not sure'
                           gene_left, gene_right = get_flanking_genes(args.ref, start, end, args.cds, args.trna, args.rrna)
                           if len(seq_results) !=0:
                               results['region_' + str(region)] = [orient, str(start), str(end), info[6], 'Possible related IS?', str(seq_results[0]), str('%.2f' % seq_results[1]), gene_left[-1][:-1], gene_left[-1][-1], gene_left[1], gene_right[-1][:-1], gene_right[-1][-1], gene_right[1]]
                           else:
                                removed_results['region_' + str(region)] = line.strip() + '\tright_unpaired.bed\n'                
                        region += 1
                    #could possibly be a novel hit but the gap size is too large
                    elif float(info[6]) / is_length <= args.min_range and float(info[6]) / is_length < args.max_range:

                        novel_hit(x_L, y_L, x_R, y_R, x, y, genbank, args.ref, args.cds, args.trna, args.rrna, info[6], orient, feature_count, region, results, unpaired=True)
                        region +=1
                    #this is something else altogether - either the gap is really large or something, place it in removed_results
                    else:
                        removed_results['region_' + str(region)] = line.strip() + '\tright_unpaired.bed\n'
                        region += 1

    #sort regions into the correct order
    table_keys = []
    for key in results:
        table_keys.append(key)
    region_indexes = []
    for region in table_keys:
        region_indexes.append(region.split('region_')[1])
    arr = np.vstack((table_keys, region_indexes)).transpose()
    if arr != 0:
        sorted_keys = arr[arr[:,1].astype('int').argsort()]

    #write out the found hits to file
    output = open(args.output + '_table.txt', 'w')
    output.write('\t'.join(header) + '\n')
    if arr != 0:
        for key in sorted_keys[:,0]:
            output.write(key + '\t' + '\t'.join(str(i) for i in results[key]) + '\n')
    if arr == 0:
        output.write('No hits found.')
    output.close()

    #write out hits that were removed for whatever reason to file
    if len(removed_results) != 0:
        output_removed = open(args.output + '_removedHits.txt', 'w')
        for region in removed_results:
            output_removed.write(removed_results[region])
        output_removed.close()

    SeqIO.write(genbank, args.output + '_annotated.gbk', 'genbank')
    print('Added ' + str(feature_count) + ' features to ' + args.output + '_annotated.gbk')

    #return(lines, len(removed_results))

if __name__ == "__main__":
    main()