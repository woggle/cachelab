from django.db import models
import logging
import json
import math
import random
import uuid

logger = logging.getLogger('cachelabweb')

class CacheAccess():
    def __init__(self, data, default_size=1):
        self.type = data['type']  # read or write
        self.address = data['address']
        self.size = data.get('size', default_size)

    @property
    def address_hex(self):
        return hex(self.address)

    def as_dump(self):
        return {
            'type': self.type,
            'address': self.address,
            'size': self.size
        }


class CacheEntry():
    def __init__(self, data):
        self.valid = data['valid']
        self.lru = data['lru']
        if self.valid:
            self.tag = data['tag']
            self.dirty = data['dirty']
        else:
            self.tag = self.dirty = None

    def as_dump(self):
        return {
            'valid': self.valid,
            'tag': self.tag,
            'lru': self.lru,
            'dirty': self.dirty
        }

    def __repr__(self):
        return 'CacheEntry(%s)' % (self.as_dump())

class CacheParameters(models.Model):
    num_ways = models.IntegerField(default=2)
    num_indices = models.IntegerField(default=4)
    entry_size = models.IntegerField(default=8)
    # FIXME: is_writeback support

    @property
    def offset_bits(self):
        return int(math.log2(self.entry_size))
    
    @property
    def index_bits(self):
        return int(math.log2(self.num_indices))

    def split_address(self, address):
        offset = address & ~((~0) << self.offset_bits)
        index = (address >> self.offset_bits) & ~((~0) << self.index_bits)
        tag = (address >> (self.offset_bits + self.index_bits))
        return (tag, index, offset)

def _update_lru(entry_list, new_most_recent):
    new_most_recent.lru = len(entry_list)
    entry_list.sort(key=lambda e: e.lru)
    for i, e in enumerate(entry_list):
        e.lru = i

def _get_lru(entry_list):
    logger.debug('entry_list = %s', entry_list)
    return list(filter(lambda e: e.lru == 0, entry_list))[0]

class CacheState():
    def __init__(self, params):
        self.params = params
        entries = []
        for index in range(params.num_indices):
            entries.append([])
            for way in range(params.num_ways):
                entries[-1].append(
                    CacheEntry({
                        'valid': False,
                        'tag': None,
                        'lru': way,
                        'dirty': False
                    })
                )
        self.entries = entries

    def to_entries(self):
        return self.entries

    def apply_access(self, access):
        (tag, index, offset) = self.params.split_address(access.address)
        found = None
        entries_at_index = self.entries[index]
        was_hit = False
        for possible in entries_at_index:
            if possible.tag == tag:
                found = possible
                was_hit = True
                break
        if found == None:
            # FIXME: record dirty flush here
            found = _get_lru(entries_at_index)
            found.valid = True
            found.tag = tag
            if access.type == 'W':
                found.dirty = True
        _update_lru(entries_at_index, found)
        return was_hit

    def to_json(self):
        return json.dumps(list(
            map(lambda row: list(map(lambda x: x.as_dump(), row)),
                self.entries)
        ))

    @staticmethod
    def from_json(params, the_json):
        raw_data = json.loads(the_json)
        result = []
        for raw_row in raw_data:
            row = []
            for raw_entry in raw_row:
                row.append(CacheEntry(raw_entry))
            result.append(row)
        state = CacheState(params)
        state.entries = result

class CachePattern(models.Model):
    pattern_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parameters = models.ForeignKey('CacheParameters', on_delete=models.PROTECT)
    access_size = models.IntegerField(default=2)
    address_bits = models.IntegerField(default=6)
    # JSON list of cache accesses
    accesses_raw = models.TextField()
    # JSON list of "is hit"
    access_results_raw = models.TextField()
    # JSON list of cache entries
        # FIXME: don't store?
    final_state_raw = models.TextField()

    def accesses(self):
        return list(map(CacheAccess, json.loads(self.accesses_raw)))

    def access_results(self):
        return json.loads(self.access_results_raw)

    def final_state(self):
        return CacheState.from_json(self.final_state_raw)

    def generate_results(self):
        state = CacheState(self.parameters)
        results = []
        for access in self.accesses():
            results.append(state.apply_access(access))
        self.access_results_raw = json.dumps(results)
        self.final_state_raw = state.to_json()

    @staticmethod
    def generate_random(parameters, num_accesses=50):
        result = CachePattern()
        result.parameters = parameters
        accesses =  []
        for i in range(num_accesses):
            address = random.randrange(0, 1 << result.address_bits)
            address &= ~(result.access_size - 1)
            accesses.append(CacheAccess({'type':'R', 'address':address, 'size':result.access_size}))
        result.accesses_raw = json.dumps(list(map(lambda a: a.as_dump(), accesses)))
        result.generate_results()
        result.save()
        return result

    # FIXME: function to create

class CacheQuestion(models.Model):
    question_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pattern = models.ForeignKey('CachePattern', on_delete=models.PROTECT)
    for_user = models.TextField()
    index = models.IntegerField()

    @staticmethod
    def generate_random(parameters, for_user, index):
        pattern = CachePattern.generate_random(parameters)
        result = CacheQuestion()
        result.pattern = pattern
        result.for_user = for_user
        result.index = index
        return result

class CacheAnswer(models.Model):
    question = models.ForeignKey('CacheQuestion', on_delete=models.PROTECT)
    access_results_raw = models.TextField()
    final_state_raw = models.TextField()
    score = models.IntegerField()
    submit_time = models.DateTimeField(auto_now=True,editable=False)

    @property
    def access_results(self):
        return json.loads(self.access_results_raw)

    @access_results.setter
    def set_access_results(self, value):
        self.access_results_raw = json.dumps(value)

