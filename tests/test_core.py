import unittest
from drivebridge.core import plan


def file(checksum, size=10):
    return {'md5Checksum': checksum, 'size': size}


class PlanningTests(unittest.TestCase):
    def test_first_sync_conflict_does_not_overwrite(self):
        tasks, conflicts = plan({'a': file('a')}, {'a': file('b')}, {}, 'both')
        self.assertEqual(tasks, [])
        self.assertEqual(conflicts, ['a'])

    def test_remote_change_downloads(self):
        tasks, conflicts = plan({'a': file('old')}, {'a': file('new')},
            {'a': {'local': 'old', 'remote': 'old'}}, 'both')
        self.assertEqual(tasks, [('download', 'a', 10)])
        self.assertEqual(conflicts, [])

    def test_simultaneous_changes_conflict(self):
        tasks, conflicts = plan({'a': file('left')}, {'a': file('right')},
            {'a': {'local': 'old', 'remote': 'old'}}, 'both')
        self.assertFalse(tasks)
        self.assertEqual(conflicts, ['a'])

    def test_local_change_uploads(self):
        tasks, _ = plan({'a': file('new')}, {'a': file('old')},
            {'a': {'local': 'old', 'remote': 'old'}}, 'both')
        self.assertEqual(tasks, [('upload', 'a', 10)])

    def test_missing_file_is_restored_without_deletion(self):
        tasks, _ = plan({}, {'a': file('a')}, {}, 'both')
        self.assertEqual(tasks, [('download', 'a', 10)])

    def test_direction_and_identical_files(self):
        tasks, _ = plan({'a': file('a'), 'b': file('b')}, {'a': file('a')}, {}, 'upload')
        self.assertEqual(tasks, [('upload', 'b', 10)])


if __name__ == '__main__':
    unittest.main()
