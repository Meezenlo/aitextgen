import torch
import logging
import csv
import os
import msgpack
import random
import gzip
from torch.utils.data import Dataset
from typing import List
from transformers import GPT2TokenizerFast
from pkg_resources import resource_filename

logger = logging.getLogger(__name__)

STATIC_PATH = resource_filename(__name__, "static")


class TokenDataset(Dataset):
    """
    Class that merges TextDataset and LineByLineTextDataset from
    run_language_modeling.py in transformers, plus
    adds more ways to ingest text such as with CSVs.

    :param file_path: A string indicating the relative file path of the text
    to be tokenized.
    :param tokenizer: Tokenizer for the corresponding model. Defaults to GPT-2 if not specified
    :param texts: A list of input texts (if providing texts manually)
    :param line_by_line: A boolean to indicate if the input file should be read
    line by line (True) or as a full text (False).
    :param from_cache: A string indicating if loading from a pregenerated MsgPack
    dump.
    :param header: A boolean indicating if loading from a CSV, if it has a header.
    :param save_cache: A boolean indicating whether to save the tokenized
    dataset as a MsgPack dump to load later.
    :param cache_destination: A string indicating where to save the cache.
    :param block_size: An integer indicating maximum length of the text document
    (usually set by the model architecture)
    :param tokenized_texts: Texts that are already tokenized; only should
    be used by merge_datasets().
    """

    def __init__(
        self,
        file_path: str = None,
        vocab_file: str = os.path.join(STATIC_PATH, "gpt2_vocab.json"),
        merges_file: str = os.path.join(STATIC_PATH, "gpt2_merges.txt"),
        texts: List[str] = None,
        line_by_line: bool = False,
        from_cache: bool = False,
        header: bool = True,
        save_cache: bool = False,
        cache_destination: str = "dataset_cache.tar.gz",
        compress: bool = True,
        block_size: int = 1024,
        tokenized_texts: bool = False,
        bos_token: str = "<|endoftext|>",
        eos_token: str = "<|endoftext|>",
        unk_token: str = "<|endoftext|>",
        pad_token: str = "<|endoftext|>",
    ) -> None:

        # Special case; load tokenized texts immediately
        if tokenized_texts:
            self.tokens = tokenized_texts
            self.file_path = "merged TokenDataset"
            self.str_suffix = "by merging TokenDatasets."
            return

        assert any([texts, file_path]), "texts or file_path must be specified."

        tokenizer = GPT2TokenizerFast(
            vocab_file=vocab_file,
            merges_file=merges_file,
            bos_token=bos_token,
            eos_token=eos_token,
            unk_token=unk_token,
            pad_token=pad_token,
        )

        # If a cache path is provided, load it.
        if from_cache:
            open_func = gzip.open if file_path.endswith(".gz") else open

            with open_func(file_path, "rb") as f:
                self.tokens = msgpack.unpack(f)
            self.str_suffix = "via cache."

        # if texts are present, just tokenize them.
        elif texts is not None:
            text = ""
            for line in texts:
                text += str(line) + eos_token

            logger.info(f"{len(texts):,} samples loaded.")
            self.str_suffix = "via application."

        # if a file is specified, and it's line-delimited,
        # the text must be processed line-by-line into a a single bulk file
        elif line_by_line:
            assert os.path.isfile(file_path)

            text, count = read_lines_from_file(file_path, eos_token, header=header)
            logger.info(f"{count:,} samples loaded.")

            self.file_path = file_path
            self.str_suffix = f"from line-by-line file at {file_path}."

        # if a file is specified, and it's not line-delimited,
        # the texts must be parsed as a single bulk file.
        else:
            assert os.path.isfile(file_path)

            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()

            self.file_path = file_path
            self.str_suffix = f"from file at {file_path}."

        self.tokens = tokenizer.encode(text)
        assert (
            len(self.tokens) >= block_size
        ), f"There are fewer than {block_size} tokens."
        self.num_subsets = len(self.tokens) - block_size
        self.block_size = block_size

        if save_cache:
            self.save(cache_destination, compress=compress)

    def save(
        self, cache_destination: str = "dataset_cache.tar.gz", compress: bool = True
    ) -> None:
        assert len(self.examples) > 0, "No data loaded to save."

        if compress:
            open_func = gzip.open
            compress_str = "and compressing "
        else:
            open_func = open
            cache_destination = (
                "dataset_cache.msgpack"
                if cache_destination == "dataset_cache.tar.gz"
                else cache_destination
            )
            compress_str = ""

        logger.info(f"Caching {compress_str}dataset to {cache_destination}")

        with open_func(cache_destination, "wb") as f:
            msgpack.pack(self.tokens, f)

    def __len__(self):
        return self.num_subsets

    def __getitem__(self, item: int) -> torch.Tensor:
        return torch.tensor(
            self.tokens[item : (item + self.block_size)], dtype=torch.long
        )

    def __str__(self):
        return self.file_path if self.file_path is not None else "loaded dataset"

    def __repr__(self):
        return f"TokenDataset containing {self.num_subsets:,} subsets loaded {self.str_suffix}"


def read_lines_from_file(
    file_path: str, eos_token: str, header: bool = True
) -> (List[str], int):
    """
    Retrieves texts from a newline-delimited file/CSV and returns as a bulk text.
    """

    with open(file_path, "r", encoding="utf-8") as f:
        text = ""
        count = 0
        if header:
            f.readline()
        if file_path.endswith(".csv"):
            reader = csv.reader(f)
            for row in reader:
                text += row[0] + eos_token
                count += 1
        else:
            reader = f.read().splitlines()
            for line in reader:
                if len(line) > 0 and not line.isspace():
                    text += line + eos_token
                    count += 1

    return text, count


def merge_datasets(
    datasets: List[TokenDataset], equalize: bool = True, seed: int = None
) -> TokenDataset:
    """
    Merges multiple TokenDatasets into a single TokenDataset.
    This assumes that you are using the same tokenizer for all TokenDatasets.

    ## Parameters

    * **datasets**: A list of TokenDatasets.
    * **equalize**: Whether to take an equal amount of samples from all
    input datasets (by taking random samples from each dataset equal to the smallest dataset) in order to balance out the result dataset.
    * **seed**: Seed to control the random sampling, if using equalize.
    """

    assert (
        isinstance(datasets, list) and len(datasets) > 1
    ), "datasets must be a list of multiple TokenDatasets."

    len_smallest = min([len(dataset) for dataset in datasets])

    if seed:
        random.seed(seed)

    tokenized_texts = []

    for dataset in datasets:
        if equalize:
            texts_subset = random.sample(dataset.examples, len_smallest)
            tokenized_texts.extend(texts_subset)
        else:
            tokenized_texts.extend(dataset.examples)

    # Reset seed
    if seed:
        random.seed()

    return TokenDataset(tokenized_texts=tokenized_texts)
