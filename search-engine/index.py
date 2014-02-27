#!/usr/bin/python
# -*- coding: utf-8 -*-
# XXX somehow we need to handle too many chunks in a segment. Maybe subdirs %100.
from __future__ import print_function

import gzip
import heapq
import itertools
import os
import re
import sys
import shutil
import traceback

class Path:                     # like java.lang.File
    def __init__(self, name):
        self.name = name
    __getitem__  = lambda self, child: Path(os.path.join(self.name, str(child)))
    __contains__ = lambda self, child: os.path.exists(self[child].name)
    __iter__     = lambda self: (self[child] for child in os.listdir(self.name))
    open         = lambda self, *args: open(self.name, *args)
    open_gzipped = lambda self, *args: gzip.GzipFile(self.name, *args)

def postings_from_dir(path):
    # XXX re.compile?
    for dir_name, _, filenames in os.walk(path.name):
        dir_path = Path(dir_name)
        for filename in filenames:
            with dir_path[filename].open() as fo:
                seen_words = set()
                for line in fo:
                    for word in re.findall('\w+', line):
                        if word not in seen_words:
                            yield word, dir_path[filename].name
                            seen_words.add(word)

def dir_metadata(path):
    for dirpath, _, filenames in os.walk(path.name):
        for filename in filenames:
            pathname = os.path.join(dirpath, filename)
            s = os.stat(pathname)
            yield pathname, s.st_size, s.st_mtime

def write_tuples(context_manager, tuples):
    with context_manager as outfile:
        for item in tuples:
            outfile.write(' '.join(map(str, item)) + "\n")

def read_tuples(context_manager):
    with context_manager as infile:
        for line in infile:
            yield tuple(line.split())

# XXX maybe these functions don't need to exist?
def write_metadata(ath, metadata):
    write_tuples(path['metadata'].open('w'), metadata)

def read_metadata(path):
    tuples = read_tuples(path['metadata'].open())
    return dict((pathname, (size, mtime)) for pathname, size, mtime in tuples)

def file_unchanged(metadatas, path):
    s = os.stat(path.name)
    return any(metadata.get(path.name) == (s.st_size, s.st_mtime)
               for metadata in metadatas)

# XXX this should probably be called like "break_into_segments" or
# some shit.  2**20 is chosen as the standard max_chunk_size (XXX
# max_segment_size) because that uses typically about a quarter gig, (XXX test)
# which is a reasonable size these days.
def sorted_uniq_chunks(iterator, max_chunk_size=2**20):
    chunk = []
    for item in iterator:
        chunk.append(item)
        if len(chunk) == max_chunk_size:
            chunk.sort()
            yield chunk
            chunk = []

    if chunk:
        chunk.sort()
        yield chunk

# From some answer on Stack Overflow.
def break_up(seq, chunk_size=4096):
    seq = iter(seq)
    while True:
        yield tuple(itertools.islice(seq, chunk_size)) or next(seq)

def write_new_segment(path, postings):
    os.mkdir(path.name)
    for ii, chunk in enumerate(break_up(postings)):
        write_tuples(path['%s.gz' % ii].open_gzipped('w'), chunk)
    build_skip_file(path)

def merge_segments(path, segments):
    if len(segments) == 1:
        return

    postings = heapq.merge(*[read_segment(segment)
                             for segment in segments])
    ii = 0 # XXX factor out
    while ii in path:
        ii += 1
    write_new_segment(path[ii], postings)

    for segment in segments:
        shutil.rmtree(segment.name)

def read_segment(path):
    for _, chunk in skip_file_entries(path):
        # XXX refactor chunk reading?  We open_gzipped in three places now.
        for item in read_tuples(path[chunk].open_gzipped()):
            yield item

def pathnames(index_path, terms):
    "Actually evaluate a query."
    return set.intersection(*(set(term_pathnames(index_path, term))
                              for term in terms))

def term_pathnames(index_path, term):
    return itertools.chain.from_iterable(segment_term_pathnames(segment, term)
                                         for segment in index_path)

def segment_term_pathnames(segment, term):
    for chunk_name in segment_term_chunks(segment, term):
        # XXX need to close the read_tuples() generator!
        for term_2, pathname in read_tuples(segment[chunk_name].open_gzipped()):
            if term_2 == term:
                yield pathname
            if term_2 > term:   # Once we reach an alphabetically later term,
                break           # we're done.

# XXX maybe return Path objects?
def segment_term_chunks(segment, term):
    for headword, chunk in skip_file_entries(segment):
        if headword >= term:
            yield last_chunk
        if headword > term:
            break

        last_chunk = chunk
    else:                   # executed if we don't break
        # XXX what if it was empty?
        yield last_chunk

def skip_file_entries(indexdir):
    # XXX is sorted() guaranteed correct?
    return sorted(read_tuples(indexdir['skip'].open()))

def generate_skip_entries(chunk_paths):
    for chunk_path in chunk_paths:
        # Not using read_tuples here because we'd have to explicitly close it.
        with chunk_path.open_gzipped() as chunk_file:
            word, _ = chunk_file.readline().split()
            yield word, os.path.basename(chunk_path.name)

def build_skip_file(path):
    chunk_names = list(path)
    # XXX what about fsync?
    write_tuples(path['skip'].open('w'), generate_skip_entries(chunk_names))

def build_index(index_path, corpus_path):
    os.mkdir(index_path.name)
    postings = postings_from_dir(corpus_path)
    for ii, chunk in enumerate(sorted_uniq_chunks(postings)):
        write_new_segment(index_path[ii], chunk)
    merge_segments(index_path, list(index_path))

def grep(index_path, terms):
    for pathname in pathnames(index_path, terms):
        try:
            with open(pathname) as text:
                for ii, line in enumerate(text):
                    if any(term in line for term in terms):
                        sys.stdout.write("%s:%s:%s" % (pathname, ii+1, line))
        except:                 # The file might e.g. no longer exist.
            traceback.print_exc()

if __name__ == '__main__':
    if sys.argv[1] == 'index':
        build_index(index_path=Path(sys.argv[2]), corpus_path=Path(sys.argv[3]))
    elif sys.argv[1] == 'query':
        for pathname in pathnames(Path(sys.argv[2]), sys.argv[3:]):
            print(pathname)
    elif sys.argv[1] == 'grep':
        grep(Path(sys.argv[2]), sys.argv[3:])
    else:
        raise Exception("%s (index|query|grep) index_dir ..." % (sys.argv[0]))
