## MECH50010 Fluid Mechanics 2 Problem Set \#1

## Introduction

### 1.1 Hydraulic scale

- 5-10 mins

This is a gentle warm up question to get into the swing of things after a long summer.

(a) Final answer
$$
m=0.106 \mathrm{~kg}
$$

Worked solutions
The weight of the piston applies a pressure $p=4 m g /\left(\pi D^{2}\right)$. This pressure adds to the atmospheric pressure, so that the pressure on the mercury on the left-hand side of the tube is $p+p_{a t}$. Applying the hydrostatic equations (or Bernoulli's equations for steady, inviscid and irrotational fluids) we find:

- $$
p+p_{\mathrm{at}}=p_{\mathrm{at}}+\rho_{\mathrm{w}} \sigma_{\mathrm{Hg}} g h
$$
where $\sigma$ is 'specific gravity'(density relative to water).

Hence:

- $$
\begin{aligned}
\frac{4 m g}{\pi D^{2}} & =\rho_{\mathrm{w}} \sigma_{\mathrm{Hg}} g h \\
m & =\frac{\pi}{4} \rho_{\mathrm{w}} \sigma_{\mathrm{Hg}} h D^{2} \\
& =\frac{\pi}{4} * 1000 * 13.54 * 10^{-3} * 10^{-2} \\
& =0.106 \mathrm{~kg}
\end{aligned}
$$
(b)

Final answer
Selecting a fluid with a smaller specific gravity.

Worked solutions
To improve the accuracy of the measurement, $D$, being fixed by the piston's size, it is best to makehbigger (this is what is read), which translates in selecting a fluid with a smaller specific gravity.

### 1.2 Friction on a plate

- ★ 15-20 mins

This question is to refresh yourself using control volumes, which were used in ME1 and will be used often in ME2.

(a)

Final answer

$$
\dot{m}=30 \mathrm{~kg} / \mathrm{s}
$$

Worked solutions
From mass conservation:

$$
-\dot{m}_{a d}=\dot{m}_{a b}+\dot{m}_{b c}-
$$

This becomes:

$$
-\rho W \int_{0}^{\delta} U_{0} \mathrm{~d} y=\dot{m}_{a b}+\rho W \int_{0}^{\delta} u(y) \mathrm{d} y-
$$

Since we know that $u(y)$ is linear, we can express it in terms of $y$ and $U_{0}$ :

$$
u(y)=U_{0} \frac{y}{\delta},
$$

Therefore:

$$
\begin{aligned}
\dot{m}_{a b} & =\rho W U_{0}\left(\int_{0}^{\delta} 1-\frac{y}{\delta} \mathrm{~d} y\right) \\
\dot{m}_{a b} & =\rho W U_{0} \frac{\delta}{2} \\
\dot{m}_{a b} & =(800)(1)(3) \frac{25 \times 10^{-3}}{2} . \\
& \dot{m}=30 \mathrm{~kg} / \mathrm{s}
\end{aligned}
$$

(b)

Final answer

$$
F_{\text {plate }}=30 \mathrm{~N}
$$

Worked solutions
From momentum conservation:

$$
F_{\text {fluid }}=M_{a b}+M_{b c}-M_{a d}
$$

Since we are calculating the horizontal resultant force, we need to consider the horizontal momentum in the section "ab"; this means that we ignore any vertical velocities. Since the velocity of the fluid at $y=\delta$ (i.e. at "ab") is always $U_{0}$, we can say:

$$
M_{a b}=U_{0} \dot{m}_{a b},
$$

We then also convert the other momentum flowrate terms into their mathematical forms, and proceed to find the force of the plate on the fluid, as shown below:

$$
F_{\text {fluid }}=M_{a b}+\rho W \int_{0}^{\delta} u(y)^{2} \mathrm{dy}-\rho W \int_{0}^{\delta} U_{0}^{2} \mathrm{dy}
$$

$$
F_{\text {fluid }}=U_{0} \dot{m}_{a b}+\rho W U_{0}^{2}\left(\int_{0}^{\delta} \frac{y^{2}}{\delta^{2}}-1 \mathrm{dy}\right)
$$

$$
F_{\text {fluid }}=(3)(30)+(800)(1)(3)^{2}\left[-\frac{2}{3}\left(25 \times 10^{-3}\right)\right]
$$

$$
F_{\text {fluid }}=-30 \mathrm{~N}
$$

However, the question asks us to find the drag force of the fluid on the plate, hence:

$$
F_{\text {plate }}=-F_{\text {fluid }}
$$

$$
F_{\text {plate }}=30 \mathrm{~N}
$$

### 1.3 Towing a submarine

- 15-20 mins

This is another revision question from ME1, for those who need extra practice.
(a)

Final answer

$$
F=\frac{\pi}{6} \rho U^{2} R^{2}
$$

Worked solutions
The mass flow rate entering the control volume is

$$
\dot{m}_{\mathrm{in}}=\rho U A_{\mathrm{disk}}=\rho U \pi R^{2}
$$

- 

(since the velocity is uniform). The mass exiting the control volume on the left (the wake) is:
-

$$
\dot{m}_{\text {wake }}=2 \pi \rho \int_{r=0}^{r=R} r(U r / R) \mathrm{d} r
$$

Hence, the mass flow rate leaving the control volume through the side $\left(\dot{m}_{\text {side }}\right)$ is:

$$
\begin{aligned}
\dot{m}_{\text {side }} & =\dot{m}_{\text {in }}-\dot{m}_{\text {wake }} \\
& =\rho U \pi R^{2}-2 \rho U \pi \int_{r=0}^{r=R} \frac{r^{2}}{R} \mathrm{~d} r \\
& =\rho U \pi R^{2}-2 \rho U \pi\left[\frac{1}{3} \frac{r^{3}}{R}\right]_{r=0}^{r=R} \\
& =\rho U \pi R^{2}-\frac{2}{3} \rho U \pi R^{2} \\
& =\frac{1}{3} \rho U \pi R^{2}
\end{aligned}
$$

Force-momentum equation (FME):

The momentum flowrate entering the control volume is $M_{\text {in }}=\dot{m}_{\text {in }} U$. The momentum flowrate exiting the control volume on the left (the wake) is
-

$$
M_{\text {wake }}=2 \pi \rho \int_{r=0}^{r=R} r(U r / R)^{2} \mathrm{~d} r
$$

- 

The mass leaving the control volume by the side is also contributing to the removal of momentum, $M_{\text {side }}=\dot{m}_{\text {side }} U$. Hence, the momentum leaving the control volume is $M_{\text {out }}=M_{\text {wake }}+M_{\text {side }}$.

The FME reads:
-

$$
M_{\text {out }}-M_{\text {in }}=-F+F_{p},
$$

- 

where $F_{p}$ represents pressure forces. However, we assume the pressure to be unaffected by the presence of the submarine, which leaves the hydrostatic pressure force. Since the hydrostatic pressure has the same linear profile on both sides of the control volume (front and wake), it contributes nothing to the horizontal force. Hence:
-

$$
\begin{aligned}
-F & =2 \pi \rho \int_{r=0}^{r=R} r(U r / R)^{2} \mathrm{~d} r+\dot{m}_{\text {side }} U-\dot{m}_{\text {in }} U \\
& =2 \rho U^{2} \pi \int_{r=0}^{r=R}\left(r^{3} / R^{2}\right) \mathrm{d} r+\frac{1}{3} \rho U^{2} \pi R^{2}-\rho U^{2} \pi R^{2} \\
& =2 \rho U^{2} \pi\left[\frac{1}{4} \frac{r^{4}}{R^{2}}\right]_{r=0}^{r=R}-\frac{2}{3} \rho U^{2} \pi R^{2} \\
& =\frac{1}{2} \rho U^{2} \pi R^{2}-\frac{2}{3} \rho U^{2} \pi R^{2}
\end{aligned}
$$

- 

Which gives:

$$
F=\frac{\pi}{6} \rho U^{2} R^{2}
$$

(b)

Final answer

$$
P=\frac{\pi}{6} \rho U^{3} R^{2}
$$

Worked solutions
By definition, the power is $F . U$. Therefore:

$$
P=\frac{\pi}{6} \rho U^{3} R^{2}
$$

### 1.4 Molecules, particles, and continuum

- ★ 20-25 mins

This question bridges ME1 and ME2, exploring the continuum hypothesis and the definition of a fluid particle.
(a)

Final answer

$$
n_{0} \approx 2.65 \times 10^{25} \mathrm{~m}^{-3} \text { (molecules per metre cubed) }
$$

To enter in the checker, for example:
$2.65 e 25 \mathrm{~m}^{\wedge}(-3)$

Worked solutions
The fluid density is directly related to the particle density: $\rho_{0}=n_{0} m / V$ (assuming all molecules to be the same). In this question, we take $V$ to be a cubic meter. Therefore, we need to calculate $\rho_{0}$ and $m$ (the mass of one molecule).

The density can be computed from the ideal-gas law (the gas is assumed to be ideal): $\rho_{0}=p_{0} /\left(R T_{0}\right)$ where $R=\tilde{R} / M$.

The mass is directly computed from the Avogadro number and the molecular mass: $m=M / \mathcal{N}_{A}$.

Hence:

$$
n_{0}=\frac{p_{0} \mathcal{N}_{A} V}{\tilde{R} T_{0}}=\frac{\left(10^{5}\right)\left(6.02 \times 10^{23}\right)(1)}{(8.314)(273.15)}
$$

$$
n_{0} \approx 2.65 \times 10^{25} \text { molecules per metre cubed }
$$

(b)

Final answer

$$
\begin{aligned}
& \ell_{0} \approx 3.58 \times 10^{-7} \mathrm{~m} \\
& 0.1 \mathrm{~mm}<d<1 \mathrm{~mm} .
\end{aligned}
$$

Worked solutions

$$
\ell_{0}=\frac{1}{\sqrt{2} \pi \sigma^{2} n_{0}}=\frac{1}{\sqrt{2} \pi\left(1.54 \times 10^{-10}\right)^{2}\left(2.65 \times 10^{25}\right)} \approx 3.58 \times 10^{-7} \mathrm{~m}
$$

The fluid particle size $d$ must be such that:

$$
10^{-7} \mathrm{~m} \ll d \ll 10^{0} \mathrm{~m} \quad \longrightarrow \quad 0.1 \mathrm{~mm}<d<1 \mathrm{~mm}
$$

(c)

Final answer
The designers'assumption appears to be adequate.

Worked solutions
First, we show that the density ratio $\rho / \rho_{0}$ can be written in terms of particle densities:

$$
\frac{\rho}{\rho_{0}}=\frac{n m}{V} \cdot \frac{V}{n_{0} m}=\frac{n}{n_{0}}
$$

From part (b) the mean free path is given by $\ell=1 /\left(\sqrt{2} \pi \sigma^{2} n\right)$, hence:

$$
\frac{\rho}{\rho_{0}}=\frac{n}{n_{0}}=\frac{\sqrt{2} \pi \sigma^{2} \ell_{0}}{\sqrt{2} \pi \sigma^{2} \ell}=\frac{\ell_{0}}{\ell}
$$

For an altitude of $H=10 \mathrm{~km}$,

$$
\frac{\rho_{z=H}}{\rho_{0}}=\frac{\ell_{0}}{\ell_{z=H}}=\left[1-\frac{g}{c_{p} T_{0}} H\right]^{1 /(\gamma-1)}
$$

Rearranging for the mean-free path at altitude,

$$
\begin{aligned}
\ell_{z=H} & =\ell_{0}\left[1-\frac{g}{c_{p} T_{0}} H\right]^{-1 /(\gamma-1)} \\
\ell_{z=H} & =3.58 \times 10^{-7}\left[1-\left(\frac{9.8 * 10^{4}}{830 * 273.15}\right)\right]^{(-5 / 2)} \\
& =1.5 \mu \mathrm{~m}
\end{aligned}
$$

where $\ell_{0}$ is taken from part (b). For the engineering length scale $L=1$ ma particle size $\ell \ll d \ll L$ is still (more-or-less!) possible. The requirement is not a strict ratio of $10^{3}$ and we must be practical in our decision making. For example, the length scale of 1 m is quite rough; and there are also likely to be much larger sources of error in any computations based on the model - not least the parameters used in the computation. However, for significantly smaller engineering scales or higher altitudes, careful attention to the practical accuracy necessary from the computations carried out using the continuum assumption would be required.

### 1.5 Frames of reference

* 3-5 mins

A simple test of your basic understanding of Eulerian and Lagrangian frames of reference (content covered in Lecture 1 in ME2).

(a)

Final answer
The location of a particle at time, $t$.

(b)

Final answer

$$
\vec{x}=\vec{\chi}_{\mathrm{p}} .
$$

Final answer
A specific particle, $\vec{\chi}$, at a specific time, $t$.

Worked solutions
In mathematical notation we write

$$
\vec{u}(\vec{x}, t),
$$

meaning that the Eulerian vector velocity $\vec{u}$ can be evaluated for a given point in space in a Eulerian frame of reference, $\vec{x}$, and a point in time, $t$. This option, however, was not in the list.

There is an alternative definition, which is to choose a specific particle, $\vec{\chi}(t)$ and a point in time, $t$, because this definition provides, implicitly, a point in space and time.

It is not sufficient to define a specific particle and a point in space, firstly because the particle may not ever pass that point in space; and secondly because if it does, we cannot guarantee in general that it should pass that point once and only once, so we have not defined a unique point in time. We awarded half-points for this response because it is also not completely wrong -it is sufficient to evaluate some values of the Eulerian velocity field, but not necessarily all and not necessary unique values.

