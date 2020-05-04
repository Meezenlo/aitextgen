from transformers import (
    GPT2Model,
    GPT2TokenizerFast,
    GPT2Config,
)
from transformers.convert_gpt2_original_tf_checkpoint_to_pytorch import (
    convert_gpt2_checkpoint_to_pytorch,
)
import torch
import os
import re
import logging
from tqdm import trange
from datetime import datetime
from random import randint
from .TokenDataset import TokenDataset
import pytorch_lightning as pl
from .utils import download_gpt2, encode_text, set_seed, reset_seed, build_config
from .train import ATGTransformer
from typing import Union, Optional, List

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class aitextgen:
    """
    Class that serves as the main aitextgen object for training and generation.

    :param model: transformers model, as a string. If None, uses distilgpt2.
    :param config: transformers config for the model. If None, uses distilgpt2.
    :param cache_dir: folder path which has the current model alredy
    :param tf_gpt2: folder path to the OpenAI-distributed version of GPT-2. This
    will convert the model to PyTorch if not present.
    """

    torchscript = False

    def __init__(
        self,
        model: str = None,
        config: Union[str, GPT2Config] = None,
        tokenizer: GPT2TokenizerFast = None,
        cache_dir: str = "aitextgen",
        tf_gpt2: str = None,
        to_gpu: bool = False,
        verbose: bool = False,
        torchscript: bool = False,
    ) -> None:

        if not verbose:
            logging.getLogger("transformers.modeling_utils").setLevel(logging.WARN)

        if torchscript:
            self.torchscript = True

        if tf_gpt2 is not None:
            if model is None:
                assert tf_gpt2 in [
                    "124M",
                    "355M",
                    "774M",
                    "1558M",
                ], "Invalid GPT-2 model size."

                download_gpt2(cache_dir, tf_gpt2)

                if not os.path.isfile(os.path.join(cache_dir, "pytorch_model.bin")):
                    logger.info(
                        f"Converting the {tf_gpt2} GPT-2 TensorFlow weights to PyTorch."
                    )
                    convert_gpt2_checkpoint_to_pytorch(
                        os.path.join(cache_dir, tf_gpt2), "", cache_dir
                    )

                model = os.path.join(cache_dir, "pytorch_model.bin")
            logger.info(f"Loading GPT-2 model from {cache_dir}.")

            self.model = GPT2Model.from_pretrained(model, config=GPT2Config())
            self.tokenizer = GPT2TokenizerFast(cache_dir=cache_dir)

        elif model is None:
            if len(os.listdir(cache_dir)) > 0:
                logger.info(f"Loading model from {cache_dir}.")
            else:
                logger.info("Downloading distilgpt2 model.")

            if config is not None:
                if config is not isinstance(config, GPT2Config):
                    config = build_config(config, cache_dir)
            else:
                config = GPT2Config("distilgpt2")

            self.model = GPT2Model.from_config(config=config, cache_dir=cache_dir)

            if tokenizer is not None:
                logger.info(
                    "Using a custom tokenizer: it will be saved while training the model. "
                )
                self.tokenizer = tokenizer
            else:
                self.tokenizer = GPT2TokenizerFast(cache_dir=cache_dir)

        if to_gpu:
            self.to_gpu()

    def generate(
        self,
        n: int = 1,
        prompt: str = None,
        max_length: int = 200,
        temperature: float = 1.0,
        do_sample: bool = True,
        bos_token: str = None,
        eos_token: str = None,
        return_as_list: bool = False,
        seed: int = None,
    ) -> Optional[str]:
        """
        Generates texts using the stored Transformers model.
        Currently generates text using the model's generate() function.

        :param n: Numbers of texts to generate.
        :param prompt: Text to force the generated text to start with
        :param max_length: Maximum length for the generated text
        :param temperature: Determines the "creativity" of the generated text.
        The value range is different for each type of Transformer.
        :param do_sample: Samples the text, which is what we want. If False,
        the generated text will be the optimal prediction at each time,
        and therefore deterministic.
        :param bos_token: Token which indicates the start of a text.
        Uses model setting if not set.
        :param eos_token: Token which indicates the end of a text.
        Uses model setting if not set.
        :param return_as_list: Boolean which determine if text should be returned
        as a list. If False, the generated texts will be print to console.
        :param seed: A numeric seed which sets all randomness, allowing the
        generate text to be reproducible if rerunning with same parameters
        and model.
        """

        if prompt:
            prompt_text = prompt
            prompt = encode_text(prompt, self.tokenizer)

        if not bos_token:
            bos_token_id = self.tokenizer.bos_token_id

        if not eos_token:
            eos_token_id = self.tokenizer.eos_token_id

        if seed:
            set_seed(seed)

        outputs = self.model.generate(
            input_ids=prompt,
            max_length=max_length,
            temperature=temperature,
            do_sample=do_sample,
            bos_token_id=bos_token_id,
            eos_token_id=eos_token_id,
            num_return_sequences=n,
        )

        # Reset seed if used
        if seed:
            reset_seed()

        if n > 1:
            gen_texts = [
                self.tokenizer.decode(output, skip_special_tokens=True)
                for output in outputs
            ]
        else:
            gen_texts = [self.tokenizer.decode(outputs[0], skip_special_tokens=True)]

        if not return_as_list:
            if prompt is not None:
                # Bold the prompt if printing to console
                gen_texts = [
                    re.sub(
                        r"^" + prompt_text, "\033[1m" + prompt_text + "\033[0m", text,
                    )
                    for text in gen_texts
                ]

            print(*gen_texts, sep="\n" + "=" * 10 + "\n")
        else:
            return gen_texts

    def generate_one(self, **kwargs) -> None:
        """
        Generates a single text, and returns it as a string. Useful for
        returning a generated text within an API.

        See generate() for more parameters.
        """

        return self.generate(n=1, return_as_list=True, **kwargs)[0]

    def generate_samples(
        self, n: int = 3, temperatures: List[float] = [0.7, 1.0, 1.2], **kwargs
    ) -> None:
        """
        Prints multiple samples to console at specified temperatures.
        """

        for temperature in temperatures:
            print("#" * 20 + f"\nTemperature: {temperature}\n" + "#" * 20)
            self.generate(n=n, temperature=temperature, return_as_list=False, **kwargs)

    def generate_to_file(
        self,
        n: int = 20,
        batch_size: int = 5,
        destination_path: str = None,
        sample_delim: str = "=" * 20 + "\n",
        seed: int = None,
        **kwargs,
    ) -> None:

        """
        Generates a bulk amount of texts to a file, into a format
        good for manually inspecting and curating the texts.

        :param n: Number of texts to generate
        :param batch_size: Number of texts to generate simultaneously, taking
        advantage of CPU/GPU parallelization.
        :param destination_path: File name of the file. If None, a timestampped
        file name is automatically used.
        :param sample_delim: The text used to delimit each generated text.
        :param seed: Seed used for the generation. The last part of a file name
        will be the seed used to reproduce a generation.

        See generate() for more parameters.
        """

        assert n % batch_size == 0, "n must be divisible by batch_size."

        self.model = self.model.eval()

        if destination_path is None:
            # Create a time-based file name to prevent overwriting.
            # Use a 8-digit number as the seed, which is the last
            # numeric part of the file name.
            if seed is None:
                seed = randint(10 ** 7, 10 ** 8 - 1)

            destination_path = f"ATG_{datetime.utcnow():%Y%m%d_%H%M%S}_{seed}.txt"

        if seed:
            set_seed(seed)

        logging.info(f"Generating {n:,} texts to {destination_path}")

        pbar = trange(n)
        f = open(destination_path, "w", encoding="utf-8")

        for _ in range(n // batch_size - 1):
            gen_texts = self.generate(n=n, return_as_list=True, **kwargs)

            for gen_text in gen_texts:
                f.write("{}\n{}".format(gen_text, sample_delim))
            pbar.update(batch_size)

        pbar.close()
        f.close()

        if seed():
            reset_seed()

    def train(
        self,
        dataset: TokenDataset = None,
        file_path: str = None,
        output_dir: str = "",
        fp16: bool = False,
        fp16_opt_level: str = "O1",
        n_gpu: int = -1,
        n_tpu_cores: int = 0,
        max_grad_norm: float = 1.0,
        gradient_accumulation_steps: int = 1,
        seed: int = None,
        learning_rate: float = 5e-3,
        weight_decay: float = 0.0,
        adam_epsilon: float = 1e-8,
        warmup_steps: int = 0,
        num_steps: int = 5000,
        loggers: List = None,
        batch_size: int = 1,
        num_workers: int = None,
        **kwargs,
    ) -> None:
        """
        Trains/finetunes the model on the provided file/dataset using pytorch-lightning.

        :param dataset: A TokenDataset containing the samples to be trained.
        :param file_path: A string containing the text to be trained (shortcut
        instead of dataset)
        :param output_dir: A string indicating where to store the resulting
        model file folder.
        :param fp16: Boolean whether to use fp16, assuming using a compatible GPU/TPU.
        :param fp16_opt_level: Option level for FP16/APEX training.
        :param n_gpu: Number of GPU to use (-1 implies all available GPUs)
        :param n_tpu_cores: Number of TPU cores to use (should be a multiple of 8)
        :param max_grad_norm: Maximum gradient normalization
        :param gradient_accumulation_steps: Number of gradient acc steps; can be increased
        to avoid going out-of-memory
        :param seed: Interger representing the training seed.
        :param learning_rate: Training learnign rate for the default AdamW optimizer.
        :param weight_decay: Weight decay for the default AdamW optimizer.
        :param warmup_steps: Warmrup steps for the default AdamW optimizer.
        :param num_steps: Number of samples through the dataset.
        :param callbacks: pytorch-lightning callbacks.
        :param loggers: pytorch-lightning logger(s) to log results.
        """

        assert any(
            [dataset, file_path]
        ), "Either dataset or file_path must be specified"
        assert not self.torchscript, "You cannot train a traced TorchScript model."

        self.model = self.model.train()

        if file_path:
            dataset = TokenDataset(
                tokenizer=self.tokenizer,
                file_path=file_path,
                block_size=self.model.config["n_positions"],
                **kwargs,
            )

        if num_workers is None:
            # Use all CPU cores as workers if not training on CPU
            if n_gpu > 0 or n_tpu_cores > 0:
                num_workers = os.cpu_count()
            else:
                num_workers = 0

        hparams = dict(
            weight_decay=weight_decay,
            learning_rate=learning_rate,
            adam_epsilon=adam_epsilon,
            warmup_steps=warmup_steps,
            batch_size=batch_size,
            num_steps=num_steps,
            pin_memory=True if n_gpu > 0 else False,
            num_workers=num_workers,
        )

        pad = dict(
            pad_token=self.tokenizer._pad_token,
            pad_token_id=self.tokenizer.pad_token_id,
        )

        # Wrap the model in a pytorch-lightning module
        train_model = ATGTransformer(self.model, dataset, hparams, pad)

        # Begin training
        if seed:
            set_seed(seed)

        if os.path.exists(output_dir) and os.listdir(output_dir):
            raise ValueError(
                "Output directory ({}) already exists and is not empty.".format(
                    output_dir
                )
            )

        train_params = dict(
            accumulate_grad_batches=gradient_accumulation_steps,
            gpus=n_gpu,
            max_steps=num_steps,
            show_progress_bar=True,
            gradient_clip_val=max_grad_norm,
            # checkpoint_callback=checkpoint_callback,
            # check_val_every_n_epoch=0,
            logger=False,
            disable_validation=True,
            weights_summary=None,
        )

        if fp16:
            train_params["precision"] = 16 if fp16 else 32
            train_params["amp_level"] = fp16_opt_level

        if n_tpu_cores > 0:
            train_params["num_tpu_cores"] = n_tpu_cores
            train_params["gpus"] = 0

        # benchmark gives a boost for GPUs if input size is constant
        if n_gpu > 0:
            train_params["benchmark"] = True

        if n_gpu > 1:
            train_params["distributed_backend"] = "ddp"

        trainer = pl.Trainer(**train_params)
        trainer.fit(train_model)

        self.model = train_model.model

        if seed:
            reset_seed()

    def cross_train(
        self,
        inputs: List[TokenDataset],
        learning_rate: Union[float, List[float]] = 5e-3,
        num_steps: Union[int, List[int]] = 5000,
        **kwargs,
    ) -> None:
        """Trains a model across multiple input datasets, with automatic
        decay after each run."""

        datasets = [
            TokenDataset(tokenizer=self.tokenizer, file_path=x, **kwargs)
            if isinstance(x, str)
            else x
            for x in inputs
        ]

        if not isinstance(learning_rate, list):
            learning_rate = [learning_rate / x for x in range(len(datasets))]

        if not isinstance(num_steps, list):
            num_steps = [int(num_steps / x) for x in range(len(datasets))]

        assert len(datasets) == len(learning_rate) == len(num_steps), (
            "The provided learning_rates or num_steps"
            + " is not equal to the number of inputs."
        )

        for i, dataset in enumerate(datasets):
            logger.info(f"Now training on {dataset} for {num_steps[i]:,} steps.")
            self.train(
                dataset=dataset,
                learning_rate=learning_rate[i],
                num_steps=num_steps[i],
                **kwargs,
            )

    def export(self, for_gpu: bool = False) -> None:
        """Exports the model to TorchScript.

        for_gpu should be set to True if the resulting model is intended to
        be run on a GPU.
        """

        if for_gpu:
            self.to_gpu()
        else:
            self.to_cpu()

        example = torch.tensor([self.tokenizer.encode("")])
        traced_model = torch.jit.trace(self.model.eval(), example)
        traced_model.save("model.pt")

    def to_gpu(self, index: int = 0) -> None:
        """Moves the model to the specified GPU."""

        assert torch.cuda.is_available(), "CUDA is not installed."

        self.model.to(torch.device("cuda", index))

    def to_cpu(self, index: int = 0) -> None:
        """Moves the model to the specified CPU."""

        self.model.to(torch.device("cpu", index))
