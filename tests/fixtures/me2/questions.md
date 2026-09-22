## MECH50010 Fluid Mechanics 2 Problem Set \#1 Introduction

### 1.1 Hydraulic scale

5-10 mins
This is a gentle warm up question to get into the swing of things after a long summer.
A piston of diameter $D=0.1 \mathrm{~m}$ is fitted inside a U-shaped tube filled with liquid mercury (with density in $\rho_{\mathrm{Hg}}=$ $13,540 \mathrm{~kg} / \mathrm{m}^{3}$ ), as shown by the sketch below. The mercury rises by $h=1 \mathrm{~mm}$ under the weight of the piston.
![](media/58e96592-84f6-41d7-9ab9-2276d8808071-1.jpg)

(a) What is the mass, $m$, of the piston?
(b) If this result is to be used to measure the piston's weight, what would you do to improve the accuracy of the measurement?

### 1.2 Friction on a plate

★★ 15-20 mins
This question is to refresh yourself using control volumes, which were used in ME1 and will be used often in ME2.
A fluid with density $\rho=800 \mathrm{~kg} / \mathrm{m}^{3}$, flows at $U_{0}=3 \mathrm{~m} / \mathrm{s}$ over a flat plate of length $L=1 \mathrm{~m}$ and width $W=1 \mathrm{~m}$. At the trailing edge the boundary-layer thickness is $\delta=25 \mathrm{~mm}$. Assume the velocity profile at the trailing edge to be linear (in the image shown), and the flow to be two-dimensional.
![](media/58e96592-84f6-41d7-9ab9-2276d8808071-1-2.jpg)

(a) Compute the mass flow rate across the top surface of the control volume (noted "ab" in the figure).
(b) Determine the drag force on the plate.

### 1.3 Towing a submarine

- 15-20 mins

This is another revision question from ME1, for those who need extra practice.
A submerged submarine is towed horizontally at a steady speed $U$ in deep still water. An axially-symmetrical wake is formed behind the submarine in which the water velocity may be assumed to vary linearly from $U$ on the axis to zero at a radius of $R$. The variation of the water pressure with depth may be assumed to be unaffected by the presence of the submarine. The density of the water is $\rho$. Using a control-volume analysis, we want to find the required power to tow the submarine. For both choices of control volumes ( A and B as shown above), derive an expression for:
![](media/58e96592-84f6-41d7-9ab9-2276d8808071-2.jpg)

(a) The drag force $F$ of the submarine.
(b) The power $P$ required to tow the submarine.

### 1.4 Molecules, particles, and continuum

- 20-25 mins

This question bridges ME1 and ME2, exploring the continuum hypothesis and the definition of a fluid particle.
Let us consider still air in standard atmospheric conditions at ground level: $T_{0}=273.15 \mathrm{~K}, p_{0}=1.00 \mathrm{bar}$. For simplicity, we assume air to be made of exactly the same diatomic molecules (a fair assumption) with molar mass $M=28.8 \mathrm{~g} / \mathrm{mol}$. Each molecule is modelled as a hard sphere of diameter $\sigma=1.54 \times 10^{-10} \mathrm{~m}$. Consequently, air is considered to behave as an ideal gas. The Avogadro number is $\mathcal{N}_{A}=6.02 \times 10^{23} \mathrm{~mol}^{-1}$, and the universal gas constant is $\tilde{R}=8.314 \mathrm{~J} /(\mathrm{mol} \cdot \mathrm{K})$.

(a) Calculate the number of molecules $n_{0}$ per unit volume.

(Note that in the response area below you can use exponential notation, e.g. $5.7 \mathrm{e} 13 \mathrm{~m}^{\wedge}(-3)$ is an acceptable input - but an incorrect answer!).

(b) It can be shown that the mean-free path in the hard-sphere model is $\ell=1 /\left(\sqrt{2} \pi \sigma^{2} n\right)$. Give its numerical value at ground level:

If we are concerned with an engineering problem with length-scale $L \approx 1 \mathrm{~m}$, what should the size of a fluid particle be?

(c) Aircraft designers assume air to be a continuum medium. The density of air decreases with altitude as follows:
$$
\rho(z)=\rho_{0}\left[1-\frac{g}{c_{p} T_{0}} z\right]^{1 /(\gamma-1)}
$$

where $\rho_{0}, T_{0}$ are the density and temperature at ground level and $z$ is the altitude measured from the ground. The gravitational acceleration is $g=9.8 \mathrm{~m} / \mathrm{s}^{2}$, the specific heat at constant pressure is $c_{p}=0.83 \mathrm{~kJ} /(\mathrm{kg} \cdot \mathrm{K})$ and the heat capacity ratio is $\gamma=7 / 5$.

As density decreases, the mean-free path is expected to increase. Therefore, there must be a height $H$ from which the continuum assumption is no longer valid. Using the results and assumptions from parts (a) and (b), and assuming that the aircraft designer is concerned with scales of order one meter, is it reasonable to use the continuum model for an airline at 10 km altitude? Show your working.

- Yes
- No

### 1.5 Frames of reference

- 3-5 mins

A simple test of your basic understanding of Eulerian and Lagrangian frames of reference (content covered in Lecture 1 in ME2).

(a) What is $\vec{\chi}(t)$ ?
    - A fixed location, $x$, in space for all time, (t).
    - The Lagrange multiplier in time, $t$.
    - The location of a particle, p, at time, $t$.
    - The relation between velocity, p , and space, $x$, at time, $t$.
(b) When does $\vec{u}(\vec{x}, t)=\frac{\mathrm{d} \vec{\chi}_{\mathrm{p}}}{\mathrm{d} t}$ ?

When ...

- $t=0$.
- $\vec{x}=0$.
- $\vec{u}=0$.
- $\vec{u}=\vec{x}$.
- $\vec{x}=\vec{\chi}_{\mathrm{P}}$.
- $\vec{u}=\vec{\chi}_{\mathrm{P}}$.
- $t=t_{\mathrm{P}}$.
(c) For a Eulerian velocity field $\vec{u}(\vec{x}, t)$, which of the following would be sufficient to evaluate a particular value of the field?
- Pressure, $p$, temperature, $T$ and density, $\rho$.
- A Eulerian frame of reference.
- A region of space, $\vec{x}$, and a collection of particles $t$.
- The continuum hypothesis.
- A specific particle, $\vec{\chi}$, at a specific time, $t$.
- A specific particle, $\vec{\chi}$, and a point in space $\vec{x}$.

Generated: Wed Dec 102025 16:30:22 GMT+0000 (Coordinated Universal Time)

