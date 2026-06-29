# Gap-agent eval: web
Category: `test`
Generated: 2026-06-22T20:32:12

## Summary
- Papers: 2
- Total extract_gaps() time: 668.6s (avg 334.3s/paper)
- Total gaps extracted: 14
- Ungrounded quotes: 0

| paper_id | gaps_s | n_gaps | ungrounded |
|---|---|---|---|
| arxiv_1706.03762 | 395.52 | 7 | 0 |
| arxiv_2006.11239 | 273.07 | 7 | 0 |

---

## arxiv_1706.03762 — Attention Is All You Need
_gaps: 395.52s · 7 gaps · 0 ungrounded_
- **[future_work]** Auto-regressive decoding limits parallelism (✓)
  - description: The Transformer's decoder generates tokens one at a time in an auto-regressive fashion, so inference remains sequential even though training is fully parallelized. The authors list making generation less sequential as a research goal.
  - canonical: `sequential generation due to auto-regressive decoding`
  - quote (conclusion): "Making generation less sequential is another research goals of ours."
- **[future_work]** Not evaluated on non-text modalities (✓)
  - description: The Transformer is only evaluated on text tasks (machine translation and constituency parsing). Application to other input and output modalities such as images, audio, and video is left as future work.
  - canonical: `application to non-text modalities unexplored`
  - quote (conclusion): "We plan to extend the Transformer to problems involving input and output modalities other than text and to investigate local, restricted attention mechanisms to efficiently handle large inputs and outputs such as images, audio and video."
- **[future_work]** Full attention expensive for long sequences (✓)
  - description: Self-attention attends to all positions, which becomes computationally expensive for very long inputs. The authors propose investigating local, restricted attention mechanisms to handle large inputs and outputs efficiently.
  - canonical: `full attention too expensive for long sequences; restricted attention needed`
  - quote (conclusion): "We plan to extend the Transformer to problems involving input and output modalities other than text and to investigate local, restricted attention mechanisms to efficiently handle large inputs and outputs such as images, audio and video."
- **[caveat]** Dot-product compatibility may be insufficient (✓)
  - description: Experiments varying attention key size show that reducing d_k hurts quality, suggesting that the dot-product compatibility function may not be optimal and that a more sophisticated compatibility function could be beneficial.
  - canonical: `dot product attention compatibility function may be insufficient`
  - quote (results): "This suggests that determining compatibility is not easy and that a more sophisticated compatibility function than dot product may be beneficial."
- **[negative_result]** Label smoothing hurts perplexity (✓)
  - description: The authors note that label smoothing hurts perplexity, as the model learns to be more unsure, though it improves accuracy and BLEU score.
  - canonical: `label smoothing degrades perplexity while improving accuracy`
  - quote (training): "This hurts perplexity, as the model learns to be more unsure, but improves accuracy and BLEU score."
- **[future_work]** Self-attention computationally costly for very long sequences (✓)
  - description: Self-attention's O(n^2) complexity makes it expensive for very long sequences; the authors note that restricting attention to a neighborhood of size r could improve performance and plan to investigate this.
  - canonical: `self-attention quadratic complexity limits long-sequence efficiency`
  - quote (why_self_attention): "To improve computational performance for tasks involving very long sequences, self-attention could be restricted to considering only a neighborhood of size $r$ in the input sequence centered around the respective output position."
- **[limitation]** Prior recurrent models are inherently sequential (✓)
  - description: The paper states that recurrent models' inherently sequential nature precludes parallelization within training examples, which becomes critical at longer sequence lengths as memory constraints limit batching.
  - canonical: `recurrent models inherently sequential preventing parallelization`
  - quote (introduction): "This inherently sequential nature precludes parallelization within training examples, which becomes critical at longer sequence lengths, as memory constraints limit batching across examples."


## arxiv_2006.11239 — Denoising Diffusion Probabilistic Models
_gaps: 273.07s · 7 gaps · 0 ungrounded_
- **[limitation]** Log-likelihoods not competitive with likelihood-based models (✓)
  - description: The paper states that despite high sample quality, diffusion models do not achieve competitive log-likelihoods compared to autoregressive models, flows, and VAEs.
  - canonical: `log-likelihoods not competitive with other likelihood-based models`
  - quote (introduction): "Despite their sample quality, our models do not have competitive log likelihoods compared to other likelihood-based models (our models do, however, have log likelihoods better than the large estimates annealed importance sampling has been reported to produce for energy based models and score matching~[CITE:du2019implicit] [CITE:song2019generative])."
- **[limitation]** Majority of lossless codelength describes imperceptible details (✓)
  - description: The paper finds that more than half of the model's lossless codelength is consumed by imperceptible image details, indicating an inductive bias toward lossy compression.
  - canonical: `most bits in lossless coding describe imperceptible details`
  - quote (introduction): "We find that the majority of our models' lossless codelengths  are consumed to describe imperceptible image details~([REF:sec:coding])."
- **[future_work]** Unexplored data modalities beyond images (✓)
  - description: The authors state that diffusion models have excellent inductive biases for image data and look forward to investigating their utility in other data modalities.
  - canonical: `application to non-image data modalities unexplored`
  - quote (conclusion): "Since diffusion models seem to have excellent inductive biases for image data, we look forward to investigating their utility in other data modalities and as components in other types of generative models and machine learning systems."
- **[future_work]** Use as components in other generative models and systems (✓)
  - description: The authors propose future work on using diffusion models as components in other types of generative models and machine learning systems.
  - canonical: `use as components in other generative models and machine learning systems`
  - quote (conclusion): "Since diffusion models seem to have excellent inductive biases for image data, we look forward to investigating their utility in other data modalities and as components in other types of generative models and machine learning systems."
- **[caveat]** Lossy compression scheme not yet practical (✓)
  - description: The paper notes that its lossy compression argument is only a proof of concept because the required minimal random coding procedure is not tractable for high-dimensional data.
  - canonical: `lossy compression argument is a proof of concept not a practical system`
  - quote (extra_information): "Our lossy compression argument in [REF:sec:coding] is only a proof of concept, because [REF:alg:sending,alg:receiving] depend on a procedure such as minimal random coding~[CITE:havasi2018minimal], which is not tractable for high dimensional data."
- **[future_work]** Simple decoder could be replaced by more powerful model (✓)
  - description: The paper acknowledges that the independent discrete decoder used for L0 could be replaced by a more powerful conditional autoregressive model, but leaves this to future work.
  - canonical: `simple independent decoder could be replaced by more powerful conditional model`
  - quote (diffusion_models_and_denoising_autoencoders): "(It would be straightforward to instead incorporate a more powerful decoder like a conditional autoregressive model, but we leave that to future work.)"
- **[open_problem]** Prior diffusion models not shown to generate high quality samples (✓)
  - description: The paper states that to the best of the authors' knowledge, prior diffusion models had not been demonstrated capable of generating high quality samples comparable to GANs or autoregressive models.
  - canonical: `diffusion models previously unable to generate high quality samples`
  - quote (introduction): "Diffusion models are straightforward to define and efficient to train, but to the best of our knowledge, there has been no demonstration that they are capable of generating high quality samples."
