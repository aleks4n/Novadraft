# Summary-agent eval: web
Category: `transformers`
Generated: 2026-06-22T19:03:24

## Summary
- Papers: 1
- Total summarize() time: 17.4s (avg 17.4s/paper)

| paper_id | summary_s |
|---|---|
| arxiv_1706.03762 | 17.38 |

---

## arxiv_1706.03762 — Attention Is All You Need
_summary: 17.38s_
- paper_type: `method`
- keywords: Transformer, self-attention, multi-head attention, scaled dot-product attention, sequence transduction, machine translation, encoder-decoder, parallelization
- problem: Recurrent and convolutional sequence transduction models are inherently sequential, preventing parallelization within training examples and limiting training speed, especially at longer sequence lengths.
- approach: The Transformer is an encoder-decoder architecture composed entirely of stacked self-attention and position-wise feed-forward layers, with no recurrent or convolutional components. The encoder consists of 6 identical layers, each with a multi-head self-attention sub-layer and a feed-forward sub-layer, using residual connections and layer normalization. The decoder has 6 identical layers with an additional encoder-decoder attention sub-layer, plus masking in self-attention to preserve auto-regressive properties. The core attention mechanism is Scaled Dot-Product Attention, which computes attention as softmax(QK^T/√d_k)V, with the scaling factor preventing vanishing gradients for large key dimensions. Multi-Head Attention projects queries, keys, and values h=8 times with different learned linear projections, allowing the model to attend to information from different representation subspaces at different positions. Positional encodings are added to input embeddings to inject sequence order information.
- contributions:
  - Proposes the Transformer architecture, the first sequence transduction model based entirely on attention mechanisms, replacing recurrent and convolutional layers with multi-headed self-attention.
  - Achieves 28.4 BLEU on WMT 2014 English-to-German translation, improving over existing best results including ensembles by over 2 BLEU.
  - Establishes a new single-model state-of-the-art BLEU score of 41.8 on WMT 2014 English-to-French translation after training for 3.5 days on eight GPUs.
  - Demonstrates generalization to English constituency parsing, succeeding with both large and limited training data.
  - Introduces Scaled Dot-Product Attention with a 1/√d_k scaling factor to prevent softmax gradient issues for large key dimensions.
  - Introduces Multi-Head Attention, allowing the model to jointly attend to information from different representation subspaces at different positions.

- limitations_stated:
  - The paper does not explicitly discuss limitations of the Transformer in the conclusion or results sections beyond noting the focus on text modalities.
  - The authors state they plan to investigate local, restricted attention mechanisms to efficiently handle large inputs and outputs such as images, audio and video, implying the current global attention mechanism may not scale well to very long sequences.
  - The authors note making generation less sequential is a research goal, acknowledging that the decoder remains auto-regressive.

- open_threads:
  - Extending the Transformer to problems involving input and output modalities other than text (images, audio, video).
  - Investigating local, restricted attention mechanisms to efficiently handle large inputs and outputs.
  - Making generation less sequential (reducing auto-regressive dependency in the decoder).
  - Applying attention-based models to other tasks beyond translation and parsing.

- context_summary: The paper introduces the Transformer, a novel neural network architecture for sequence transduction that is based entirely on attention mechanisms, completely removing recurrence and convolutions. Prior sequence models (RNNs, LSTMs, GRUs) process tokens sequentially, which prevents parallelization within training examples and creates a fundamental computational bottleneck. The Transformer replaces this with stacked multi-head self-attention layers in both the encoder and decoder, enabling every position to directly attend to every other position in constant path length. The architecture uses Scaled Dot-Product Attention (with a 1/√d_k scaling factor to prevent softmax saturation), Multi-Head Attention (8 parallel attention heads for different representation subspaces), residual connections, layer normalization, and positional encodings to inject order information. On WMT 2014 English-to-German translation, the Transformer achieves 28.4 BLEU (outperforming all prior ensembles), and on English-to-French it achieves 41.8 BLEU as a single model after only 3.5 days of training on 8 GPUs. The model also generalizes to English constituency parsing. This work fundamentally changed the landscape of sequence modeling and became the foundation for virtually all subsequent large-scale language models and multimodal architectures.
