"""
microsearch
===========

A small search library.

Primarily intended to be a learning tool to teach the fundamentals of search. Or
that's what Daniel intended. I just want something small and quick that I can
wrap my head around.


Usage
-----

Example::

    import microsearch

    # Create an instance, pointing it to where the data should be stored.
    ms = microsearch.Microsearch('/tmp/microsearch')

    # Index some data.
    ms.index('email_1', {'text': "Peter,\n\nI'm going to need those TPS reports on my desk first thing tomorrow! And clean up your desk!\n\nLumbergh"})
    ms.index('email_2', {'text': 'Everyone,\n\nM-m-m-m-my red stapler has gone missing. H-h-has a-an-anyone seen it?\n\nMilton'})
    ms.index('email_3', {'text': "Peter,\n\nYeah, I'm going to need you to come in on Saturday. Don't forget those reports.\n\nLumbergh"})
    ms.index('email_4', {'text': 'How do you feel about becoming Management?\n\nThe Bobs'})

    # Search on it.
    ms.search('Peter')
    ms.search('tps report')


Documents
---------

Documents are dictionaries & look like::

    # Keys are field names.
    # Values are the field's contents.
    {
        "id": "document-1524",
        "text": "This is a blob of text. Nothing special about the text, just a typical document.",
        "created": "2012-02-18T20:19:00-0000",
    }


The Index
---------

The (inverted) index itself (represented by the index file), is also
essentially a dictionary. The difference is that the index is term-based, unlike
the field-based nature of the document::

    # Keys are terms.
    # Values are document/position information.
    index = {
        'blob': {
            'document-1524': [3],
        },
        'text': {
            'document-1524': [5, 10],
        },
        ...
    }

For this library, on disk, this is represented by a Berkeley DB.

"""
from bsddb3 import db
import hashlib
import math
import msgpack
import os
import re
import tempfile


__author__ = 'Daniel Lindsley, Alex Kritikos'
__license__ = 'BSD'
__version__ = (1, 0, 0)


class Microsearch(object):
    """
    Controls the indexing/searching of documents.

    Typical usage::

        ms = microsearch.Microsearch('/tmp/microsearch')
        ms.index('email_1', {'text': "This is a blob of text to be indexed."})
        ms.search('blob')

    """
    # A fairly standard list of "stopwords", which are words that contribute little
    # to relevance (since they are so common in English) & are to be ignored.
    STOP_WORDS = set([
        'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by',
        'for', 'if', 'in', 'into', 'is', 'it',
        'no', 'not', 'of', 'on', 'or', 's', 'such',
        't', 'that', 'the', 'their', 'then', 'there', 'these',
        'they', 'this', 'to', 'was', 'will', 'with'
    ])
    PUNCTUATION = re.compile('[~`!@#$%^&*()+={\[}\]|\\:;"\',<.>/?]')

    def __init__(self, base_directory):
        """
        Sets up the object & the data directory.

        Requires a ``base_directory`` parameter, which specifies the parent
        directory the index/document/stats data will be kept in.

        Example::

            ms = microsearch.Microsearch('/var/my_index')

        """
        self.base_directory = base_directory
        self.index_path = os.path.join(self.base_directory, 'index.db')
        self.docs_path = os.path.join(self.base_directory, 'documents.db')
        self.stats_path = os.path.join(self.base_directory, 'stats.msgpack')
        self.setup()

    def setup(self):
        """
        Handles the creation of the various data directories.

        If the paths do not exist, it will create them. As a side effect, you
        must have read/write access to the location you're trying to create
        the data at.
        """
        if not os.path.exists(self.base_directory):
            os.makedirs(self.base_directory)

        self.db = db.DB()
        self.db.open(self.index_path, None, db.DB_HASH, db.DB_CREATE)
        self.docs_db = db.DB()
        self.docs_db.open(self.docs_path, None, db.DB_HASH, db.DB_CREATE)
        return True


    def read_stats(self):
        """
        Reads the index-wide stats.

        If the stats do not exist, it makes returns data with the current
        version of ``microsearch`` & zero docs (used in scoring).
        """
        if not os.path.exists(self.stats_path):
            return {
                'version': '.'.join([str(bit) for bit in __version__]),
                'total_docs': 0,
            }

        with open(self.stats_path, 'r') as stats_file:
            return self.unpack(stats_file.read())

    def write_stats(self, new_stats):
        """
        Writes the index-wide stats.

        Takes a ``new_stats`` parameter, which should be a dictionary of
        stat data. Example stat data::

            {
                'version': '1.0.0',
                'total_docs': 25,
            }
        """
        with open(self.stats_path, 'w') as stats_file:
            stats_file.write(self.pack(new_stats))

        return True

    def increment_total_docs(self):
        """
        Increments the total number of documents the index is aware of.

        This is important for scoring reasons & is typically called as part
        of the indexing process.
        """
        current_stats = self.read_stats()
        current_stats.setdefault('total_docs', 0)
        current_stats['total_docs'] += 1
        self.write_stats(current_stats)

    def get_total_docs(self):
        """
        Returns the total number of documents the index is aware of.
        """
        current_stats = self.read_stats()
        return int(current_stats.get('total_docs', 0))

    # =====================
    # Packing and Unpacking
    # =====================

    def pack(self, data):
        return msgpack.packb(data)

    def unpack(self, data):
        return msgpack.unpackb(data)

    # ==============================
    # Tokenization & Term Generation
    # ==============================

    def make_tokens(self, blob):
        """
        Given a string (``blob``) of text, this will return a list of tokens.

        This generally/loosely follows English sentence construction, replacing
        most punctuation with spaces, splitting on whitespace & omitting any
        tokens in ``self.STOP_WORDS``.

        You can customize behavior by overriding ``STOP_WORDS`` or
        ``PUNCTUATION`` in a subclass.
        """
        # Kill the punctuation.
        blob = self.PUNCTUATION.sub(' ', blob)
        tokens = []

        # Split on spaces.
        for token in blob.split():
            # Make sure everything is in lowercase & whitespace removed.
            token = token.lower().strip()

            if not token in self.STOP_WORDS:
                tokens.append(token)

        return tokens

    def make_ngrams(self, tokens, min_gram=3, max_gram=6):
        """
        Converts a iterable of ``tokens`` into n-grams.

        This assumes front grams (all grams made starting from the left side
        of the token).

        Optionally accepts a ``min_gram`` parameter, which takes an integer &
        controls the minimum gram length. Default is ``3``.

        Optionally accepts a ``max_gram`` parameter, which takes an integer &
        controls the maximum gram length. Default is ``6``.
        """
        terms = {}

        for position, token in enumerate(tokens):
            for window_length in range(min_gram, min(max_gram + 1, len(token) + 1)):
                # Assuming "front" grams.
                gram = token[:window_length]
                terms.setdefault(gram, [])

                if not position in terms[gram]:
                    terms[gram].append(position)

        return terms


    # ==============
    # Index Handling
    # ==============

    def update_term_info(self, orig_info, new_info):
        """
        Takes existing ``orig_info`` & ``new_info`` dicts & combines them
        intelligently.

        Used for updating term_info within the index.
        """
        # Updates are (sadly) not as simple as ``dict.update()``.
        # Iterate through the keys (documents) & manually update.
        for doc_id, positions in new_info.items():
            if not doc_id in orig_info:
                # Easy case; it's not there. Shunt it in wholesale.
                orig_info[doc_id] = positions
            else:
                # Harder; it's there. Convert to sets, update then convert back
                # to lists to accommodate ``msgpack``.
                orig_positions = set(orig_info.get(doc_id, []))
                new_positions = set(positions)
                orig_positions.update(new_positions)
                orig_info[doc_id] = list(orig_positions)

        return orig_info

    def save_term(self, term, term_info, update=False):
        """
        Writes out new index data to disk.

        Optionally takes an ``update`` parameter, which is a boolean &
        determines whether the provided ``term_info`` should overwrite or
        update the data in the index. Default is ``False`` (overwrite).
        """

        old_line = self.db.get(term)
        if not old_line:
            line = self.pack(term_info)
        else:
            if not update:
                # Overwrite the line for the update.
                line = self.pack(term_info)
            else:
                # Update the existing record.
                new_info = self.update_term_info(self.unpack(old_line), term_info)
                line = self.pack(new_info)
    
        self.db.put(term, line)
        return True

    def load_term(self, term):
        """
        Given a ``term``, this will return the ``term_info`` associated with
        the ``term``.

        If no index file exists or the term is not found, this returns an
        empty dict.
        """
        term_info = self.db.get(term)
        if term_info:
            return self.unpack(term_info)
        return {}


    # =================
    # Document Handling
    # =================

    def save_document(self, doc_id, document):
        """
        Given a ``doc_id`` string & a ``document`` dict, writes the document to
        disk.

        Uses MSGPACK as the serialization format.
        """
        self.docs_db.put(doc_id, self.pack(document))
        return True

    def load_document(self, doc_id):
        """
        Given a ``doc_id`` string, loads a given document from disk.

        Raises an exception if the document no longer exists.

        Returns the document data as a dict.
        """
        data = self.unpack(self.docs_db.get(doc_id))
        return data

    def index(self, doc_id, document):
        """
        Given a ``doc_id`` string & a ``document`` dict, does everything needed
        to save & index the document for searching.

        The ``document`` dict must have a ``text`` key, which should contain the
        blob to be indexed. All other fields are simply stored.

        Returns ``True`` on success.
        """
        # Ensure that the ``document`` looks like a dictionary.
        if not hasattr(document, 'items'):
            raise AttributeError('You must provide `index` with a document in the form of a dictionary.')

        # For example purposes, we only index the ``text`` field.
        if not 'text' in document:
            raise KeyError('You must provide `index` with a document with a `text` field in it.')

        # Make sure the document ID is a string.
        doc_id = str(doc_id)
        self.save_document(doc_id, document)

        # Start analysis & indexing.
        tokens = self.make_tokens(document.get('text', ''))
        terms = self.make_ngrams(tokens)

        for term, positions in terms.items():
            self.save_term(term, {doc_id: positions}, update=True)

        self.increment_total_docs()
        return True


    # =========
    # Searching
    # =========

    def parse_query(self, query):
        """
        Given a ``query`` string, converts it into terms for searching in the
        index.

        Returns a list of terms.
        """
        tokens = self.make_tokens(query)
        return self.make_ngrams(tokens)

    def collect_results(self, terms):
        """
        For a list of ``terms``, collects all the documents from the index
        containing those terms.

        The returned data is a tuple of two dicts. This is done to make the
        process of scoring easy & require no further information.

        The first dict contains all the terms as keys & a count (integer) of
        the matching docs as values.

        The second dict inverts this, with ``doc_ids`` as the keys. The values
        are a nested dict, which contains the ``terms`` as the keys and a
        count of the number of positions within that doc.

        Since this is complex, an example return value::

            >>> per_term_docs, per_doc_counts = ms.collect_results(['hello', 'world'])
            >>> per_term_docs
            {
                'hello': 2,
                'world': 1
            }
            >>> per_doc_counts
            {
                'doc-1': {
                    'hello': 4
                },
                'doc-2': {
                    'hello': 1,
                    'world': 3
                }
            }

        """
        per_term_docs = {}
        per_doc_counts = {}

        for term in terms:
            term_matches = self.load_term(term)

            per_term_docs.setdefault(term, 0)
            per_term_docs[term] += len(term_matches.keys())

            for doc_id, positions in term_matches.items():
                per_doc_counts.setdefault(doc_id, {})
                per_doc_counts[doc_id].setdefault(term, 0)
                per_doc_counts[doc_id][term] += len(positions)

        return per_term_docs, per_doc_counts

    def bm25_relevance(self, terms, matches, current_doc, total_docs, b=0, k=1.2):
        """
        Given multiple inputs, performs a BM25 relevance calculation for a
        given document.

        ``terms`` should be a list of terms.

        ``matches`` should be the first dictionary back from
        ``collect_results``.

        ``current_doc`` should be the second dictionary back from
        ``collect_results``.

        ``total_docs`` should be an integer of the total docs in the index.

        Optionally accepts a ``b`` parameter, which is an integer specifying
        the length of the document. Since it doesn't vastly affect the score,
        the default is ``0``.

        Optionally accepts a ``k`` parameter. It accepts a float & is used to
        modify scores to fall into a given range. With the default of ``1.2``,
        scores typically range from ``0.4`` to ``1.0``.
        """
        # More or less borrowed from http://sphinxsearch.com/blog/2010/08/17/how-sphinx-relevance-ranking-works/.
        score = b

        for term in terms:
            idf = math.log((total_docs - matches[term] + 1.0) / matches[term]) / math.log(1.0 + total_docs)
            score = score + current_doc.get(term, 0) * idf / (current_doc.get(term, 0) + k)

        return 0.5 + score / (2 * len(terms))

    def search(self, query, offset=0, limit=20):
        """
        Given a ``query``, performs a search on the index & returns the results.

        Optionally accepts an ``offset`` parameter, which is an integer &
        controls what the starting point in the results is. Default is ``0``
        (the beginning).

        Optionally accepts a ``limit`` parameter, which is an integer &
        controls how many results to return. Default is ``20``.

        Returns a dictionary containing the ``total_hits`` (integer), which is
        a count of all the documents that matched, and ``results``, which is
        a list of results (in descending ``score`` order) & sliced to the
        provided ``offset/limit`` combination.
        """
        results = {
            'total_hits': 0,
            'results': []
        }

        if not len(query):
            return results

        total_docs = self.get_total_docs()

        if total_docs == 0:
            return results

        terms = self.parse_query(query)
        per_term_docs, per_doc_counts = self.collect_results(terms)
        scored_results = []
        final_results = []

        # Score the results per document.
        for doc_id, current_doc in per_doc_counts.items():
            scored_results.append({
                'id': doc_id,
                'score': self.bm25_relevance(terms, per_term_docs, current_doc, total_docs),
            })

        # Sort based on score.
        sorted_results = sorted(scored_results, key=lambda res: res['score'], reverse=True)
        results['total_hits'] = len(sorted_results)

        # Slice the results.
        sliced_results = sorted_results[offset:offset + limit]

        # For each result, load up the doc & update the dict.
        for res in sliced_results:
            doc_dict = self.load_document(res['id'])
            doc_dict.update(res)
            results['results'].append(doc_dict)

        return results
