# Copyright 2018 The Kubernetes Authors.
# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This file defines unit tests for functionalities in fio_workload.py"""

import unittest
from fio_workload import FioWorkload, _serialize_job_file_content, validate_fio_workload


class FioWorkloadTest(unittest.TestCase):

  def test_validate_fio_workload_empty(self):
    with self.assertRaises(Exception):
      self.assertFalse(validate_fio_workload({}), "empty-fio-workload")

  def test_validate_fio_workload_invalid_missing_bucket(self):
    self.assertFalse(
        validate_fio_workload(
            ({"fioWorkload": {}, "gcsfuseMountOptions": ""}),
            "invalid-fio-workload-missing-bucket",
        )
    )

  def test_validate_fio_workload_invalid_bucket_contains_space(self):
    with self.assertRaises(Exception):
      self.assertFalse(
          validate_fio_workload(
              ({"fioWorkload": {}, "gcsfuseMountOptions": "", "bucket": " "}),
              "invalid-fio-workload-bucket-contains-space",
          )
      )

  def test_validate_fio_workload_invalid_no_fioWorkloadSpecified(self):
    with self.assertRaises(Exception):
      self.assertFalse(
          validate_fio_workload(({"bucket": {}}), "invalid-fio-workload-2")
      )

  def test_validate_fio_workload_invalid_commented_out_fioWorkload(self):
    self.assertFalse(
        validate_fio_workload(
            ({
                "_fioWorkload": {},
                "bucket": "dummy-bucket",
                "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
            }),
            "commented-out-fio-workload",
        )
    )

  def test_validate_fio_workload_invalid_mixed_fioWorkload_dlioWorkload(self):
    self.assertFalse(
        validate_fio_workload(
            ({
                "fioWorkload": {},
                "dlioWorkload": {},
                "bucket": "dummy-bucket",
                "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
            }),
            "mixed-fio/dlio-workload",
        )
    )

  def test_validate_fio_workload_invalid_missing_fileSize(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "filesPerThread": 2,
              "numThreads": 100,
              "blockSize": "1kb",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-missing-fileSize"
          )
      )

  def test_validate_fio_workload_invalid_unsupported_fileSize(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": 1000,
              "filesPerThread": 2,
              "numThreads": 100,
              "blockSize": "1kb",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-fileSize"
          )
      )

  def test_validate_fio_workload_invalid_missing_blockSize(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "numThreads": 100,
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-missing-blockSize"
          )
      )

  def test_validate_fio_workload_invalid_unsupported_blockSize(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "blockSize": 1000,
              "filesPerThread": 2,
              "numThreads": 100,
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-blockSize"
          )
      )

  def test_validate_fio_workload_invalid_missing_filesPerThread(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "numThreads": 100,
              "blockSize": "1kb",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-missing-filesPerThread"
          )
      )

  def test_validate_fio_workload_invalid_unsupported_filesPerThread(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": "1k",
              "numThreads": 100,
              "blockSize": "1kb",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-filesPerThread"
          )
      )

  def test_validate_fio_workload_invalid_missing_numThreads(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-missing-numThreads"
          )
      )

  def test_validate_fio_workload_invalid_unsupported_numThreads(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": "1k",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-numThreads"
          )
      )

  def test_validate_fio_workload_invalid_missing_gcsfuseMountOptions(self):
    workload = dict({
        "fioWorkload": {
            "fileSize": "1kb",
            "filesPerThread": 2,
            "blockSize": "1kb",
            "numThreads": "1k",
        },
        "bucket": "dummy-bucket",
    })
    self.assertFalse(
        validate_fio_workload(
            workload, "invalid-fio-workload-missing-gcsfuseMountOptions"
        )
    )

  def test_validate_fio_workload_invalid_unsupported_gcsfuseMountOptions(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": "1k",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": 100,
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-numThreads"
          )
      )

  def test_validate_fio_workload_invalid_gcsfuseMountOptions_contains_space(
      self,
  ):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": "1k",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "abc def",
      })
      self.assertFalse(
          validate_fio_workload(
              workload,
              "invalid-fio-workload-unsupported-gcsfuseMountOptions-contains-space",
          )
      )

  def test_validate_fio_workload_invalid_unsupported_numEpochs(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": "1k",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs",
          "numEpochs": False,
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-numEpochs"
          )
      )

  def test_validate_fio_workload_invalid_numEpochsTooLow(
      self,
  ):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": "1k",
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs",
          "numEpochs": -1,
      })
      self.assertFalse(
          validate_fio_workload(
              workload,
              "invalid-fio-workload-unsupported-numEpochs-too-low",
          )
      )

  def test_validate_fio_workload_invalid_unsupported_readTypes_1(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": 10,
              "readTypes": True,
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-readTypes-1"
          )
      )

  def test_validate_fio_workload_invalid_unsupported_readTypes_2(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": 10,
              "readTypes": ["read", 1],
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-readTypes-2"
          )
      )

  def test_validate_fio_workload_invalid_unsupported_readTypes_3(self):
    with self.assertRaises(Exception):
      workload = dict({
          "fioWorkload": {
              "fileSize": "1kb",
              "filesPerThread": 2,
              "blockSize": "1kb",
              "numThreads": 10,
              "readTypes": ["read", "write"],
          },
          "bucket": "dummy-bucket",
          "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
      })
      self.assertFalse(
          validate_fio_workload(
              workload, "invalid-fio-workload-unsupported-readTypes-3"
          )
      )

  def test_validate_fio_workload_valid_without_readTypes(self):
    workload = dict({
        "fioWorkload": {
            "fileSize": "1kb",
            "filesPerThread": 2,
            "numThreads": 100,
            "blockSize": "1kb",
        },
        "bucket": "dummy-bucket",
        "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
    })
    self.assertTrue(validate_fio_workload(workload, "valid-fio-workload-1"))

  def test_validate_fio_workload_valid_with_readTypes(self):
    workload = dict({
        "fioWorkload": {
            "fileSize": "1kb",
            "filesPerThread": 2,
            "numThreads": 100,
            "blockSize": "1kb",
            "readTypes": ["read", "randread"],
        },
        "bucket": "dummy-bucket",
        "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
    })
    self.assertTrue(validate_fio_workload(workload, "valid-fio-workload-2"))

  def test_validate_fio_workload_valid_with_single_readType(self):
    workload = dict({
        "fioWorkload": {
            "fileSize": "1kb",
            "filesPerThread": 2,
            "numThreads": 100,
            "blockSize": "1kb",
            "readTypes": ["randread"],
        },
        "bucket": "dummy-bucket",
        "gcsfuseMountOptions": "implicit-dirs,cache-max-size:-1",
    })
    self.assertTrue(validate_fio_workload(workload, "valid-fio-workload-2"))

  def test_serialize_job_file_content(self):
    cases = [
        {"rawContent": "", "expectedSerializedContent": ""},
        {
            "rawContent": r"""[global]
file_size=${FILE_SIZE}
bs=64K

[Workload]
rw=randread
directory=${DIR}
""",
            "expectedSerializedContent": (
                r"[global];file_size=\\\${FILE_SIZE};bs=64K;;[Workload];rw=randread;directory=\\\${DIR};"
            ),
        },
    ]
    for case in cases:
      self.assertEqual(
          _serialize_job_file_content(case["rawContent"]),
          case["expectedSerializedContent"],
      )


if __name__ == "__main__":
  unittest.main()
