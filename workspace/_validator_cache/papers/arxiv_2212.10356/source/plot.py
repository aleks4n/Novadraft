import numpy as np
import torch
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size': 14})

color_mapping = {64:'tab:orange', 128:'tab:blue', 256:'tab:green'}

deno = 1e4
L = 8192
ratio = 8
positions = np.arange(L)[...,None]

for bar_d in [64, 128, 256]:
    i = np.arange(bar_d//2)
    pos_embs = np.concatenate([np.sin(positions/deno**(2*i/bar_d)), np.cos(positions/deno**(2*i/bar_d))], -1)
    attns = np.matmul(pos_embs, pos_embs.T)

    y = np.flip(attns[-1], 0)
    x = np.array(list(range(len(y)))) + 1

    # the following gives better results!
    #a, b = np.polyfit(np.log(x)[:50], y[:50], 1) # y = alog(x) + b
    # and this is worse!
    a, b = np.polyfit(np.log(x), y, 1) # y = alog(x) + b

    yy = a * np.log(x) + b
    y = np.flip(y, 0) / ratio # the one reported in the arXiv draft
    yy = np.flip(yy, 0) / ratio # new log fitting result
    plt.scatter(x, y - y.max(), s=1, alpha=1.0, label=r'$\bar d$={}'.format(bar_d), c=color_mapping[bar_d])
    #plt.scatter(x, yy - y.max(), s=1, c='r', alpha=1.0)
plt.legend(markerscale=3)
plt.savefig('last_row_compare_d.pdf', bbox_inches='tight')
plt.clf()

# plot heatmap
mapping = [y[L-1-i] for i in range(L)]
attns = np.zeros((L, L))

for i in range(L):
    for j in range(L):
        attns[i][j] = mapping[abs(i-j)]
attns = attns - attns.max()
plt.imshow(attns, cmap='hot', interpolation='nearest')
plt.colorbar()
plt.savefig('heatmap.pdf', bbox_inches='tight')
