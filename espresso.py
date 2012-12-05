#cluster-dependent definitions
scratch = '/scratch'
submitdir = '$LS_SUBCWD'
jobid = '$LSB_BATCH_JID'
getprocs = ' echo -e $LSB_HOSTS | sed s/" "/"\\\\n"/g >machinefile ;'\
          +' uniq machinefile >uniqmachinefile ;'\
	  +' nodes=`wc -l <uniqmachinefile` ;'\
	  +' np=`wc -l <machinefile` '
perHostMpiExec = 'mpiexec --mca plm_rsh_agent /afs/slac.stanford.edu/package/lsf/bin.slac/gmmpirun_lsgrun.sh -machinefile uniqmachinefile -np `wc -l <uniqmachinefile`'
perProcMpiExec = 'pam -g /afs/slac/g/suncat/bin/suncat-tsmpirun -x LD_LIBRARY_PATH'

from ase.calculators.general import Calculator
import atexit
import os
import sys
import numpy as np


def checkbatch():
    p = os.popen('echo '+jobid, 'r')
    batch = (p.readline().strip()!='')
    p.close()
    return batch

def mklocaltmp(batch, odir):
    if batch:
	s = submitdir
	job = jobid
    else:
	s = '.'
	job = ''
    if odir is None:
	p = os.popen('mktemp -d '+s+'/qe"'+job+'"_XXXXX', 'r')
    else:
	p = os.popen('cd '+s+' ; mkdir -p '+odir+' ; cd '+odir+' ; pwd', 'r')
    tdir = p.readline().strip()
    p.close()
    p = os.popen('cd '+tdir+ ' ; pwd', 'r')
    tdir = p.readline().strip()
    p.close()
    return tdir

def mkscratch(batch,localtmp):
    if batch:
	pernodeexec = perHostMpiExec
	job = jobid
    else:
	pernodeexec = ''
	job = ''
    p = os.popen('mktemp -d '+scratch+'/qe"'+job+'"_XXXXX', 'r')
    tdir = p.readline().strip()
    p.close()
    if pernodeexec!='':
	cdir = os.getcwd()
	os.chdir(localtmp)
	os.system(pernodeexec + ' mkdir -p '+tdir)
	os.chdir(cdir)
    return tdir

def mpisetup(tdir):
    cdir = os.getcwd()
    os.chdir(tdir)
    p = os.popen(getprocs+' ; sh -c "echo $nodes $np"', 'r')
    os.chdir(cdir)
    nodes,np = p.readline().split()
    p.close()
    return nodes,np

def cleanup(tmp, scratch, removewf, batch, calc):
    try:
	calc.stop()
    except:
	pass
    if batch:
	pernodeexec = perHostMpiExec
    else:
	pernodeexec = ''
    if removewf:
	os.system('rm -r '+scratch+'/*wfc* 2>/dev/null')
    os.system('cp -r '+scratch+' '+tmp)
    cdir = os.getcwd()
    os.chdir(tmp)
    os.system(pernodeexec + ' rm -r '+scratch+' 2>/dev/null')
    os.chdir(cdir)

def getsubmitorcurrentdir():
    p = os.popen('echo '+submitdir, 'r')
    s = p.readline().strip()
    p.close()
    if s!='':
	return s
    else:
	return os.getcwd()


hartree = 27.21138505
rydberg = 0.5*hartree
bohr = 0.52917721092
rydberg_over_bohr = rydberg / bohr

class espresso(Calculator):
    def __init__(self, pw=350.0, dw=3500.0, nbands=-10, kpts=(1,1,1),
                       xc='PBE', spinpol=False,
		       outdir=None, calcstress=False,
		       psppath=None, smearing='mv', sigma=0.2,
		       U=None,J=None,
		       dipole={'status':False},
		       field={'status':False},
		       output={'avoidio':False, 'removewf':True},
		       convergence={'energy':5e-6,
		    		    'mixing':0.5,
		    		    'maxsteps':100,
				    'diag':'david'}):
	
	self.batch = checkbatch()
	self.localtmp = mklocaltmp(self.batch, outdir)
	if self.batch:
	    self.nodes,self.np = mpisetup(self.localtmp)
	self.scratch = mkscratch(self.batch, self.localtmp)
	if output is not None and output.has_key('removewf'):
	    removewf = output['removewf']
	else:
	    removewf = True
	atexit.register(cleanup, self.localtmp, self.scratch, removewf, self.batch, self)
	
	self.pw = pw
	self.dw = dw
	self.nbands = nbands
	self.kpts = kpts
	self.xc = xc
	self.smearing = smearing
	self.sigma = sigma
	self.spinpol = spinpol
	self.outdir = outdir
	self.calcstress = calcstress
	if psppath is None:
            try:
                self.psppath = os.environ['ESP_PSP_PATH']
            except:
                print 'Unable to find pseudopotential path.  Consider setting ESP_PSP_PATH environment variable'
                raise
	else:
	    self.psppath = psppath
	if dipole is None:
	    self.dipole = {'status':False}
	else:
	    self.dipole = dipole
	if field is None:
	    self.field = {'status':False}
	else:
	    self.field = field
	self.output = output
	self.convergence = convergence
	self.U = U
	self.J = J
	self.atoms = None
	self.started = False

    def __del__(self):
	try:
	    self.stop()
	except:
	    pass

    #espresso needs an atom with a different starting magnetization
    #to be considered as a different species;
    #this helper function creates the corresponding 'species'
    def convertmag2species(self):
	smag = [('%.3f' % m) for m in self.atoms.get_initial_magnetic_moments()]
	msym = {}
	for s,p,m in zip(smag,self.spos,self.atoms.get_masses()):
	    msym[s] = [p[0],m]
	k = msym.keys()
	k.sort()
	for i,x in enumerate(k):
	    msym[x][0] += '_'+str(i)
	self.msym = msym
	self.mkeys = k
	self.smag = smag


    def writeinputfile(self):
	if self.atoms is None:
	    raise ValueError, 'no atoms defined'
	f = open(self.localtmp+'/pw.inp', 'w')
	print >>f, '&CONTROL\n  calculation=\'relax\',\n  prefix=\'calc\','
	print >>f, '  pseudo_dir=\''+self.psppath+'\','
	print >>f, '  outdir=\'.\','
	efield = (self.field['status']==True)
	dipfield = (self.dipole['status']==True)
	if efield or dipfield:
	    print >>f, '  tefield=.true.,'
	    if dipfield:
		print >>f, '  dipfield=.true.,'
	print >>f, '  tprnfor=.true.,'
	if self.calcstress:
	    print >>f, '  tstress=.true.,'
	if self.output is not None and self.output.has_key('avoidio'):
	    if self.output['avoidio']:
		print >>f, '  disk_io=\'none\','
	
	print >>f, '/\n&SYSTEM\n  ibrav=0,\n  celldm(1)=1.8897261245650618d0,'
	print >>f, '  nat='+str(self.natoms)+','
	if self.spinpol:
	    self.convertmag2species()
	    print >>f, '  ntyp='+str(len(self.msym))+','
	else:
	    print >>f, '  ntyp='+str(self.nspec)+','
	print >>f, '  ecutwfc='+str(self.pw/rydberg)+'d0,'
	print >>f, '  ecutrho='+str(self.dw/rydberg)+'d0,'
	if self.nbands is not None:
	    if self.nbands>0:
		print >>f, '  nbnd='+str(self.nbands)+','
	    else:
		n = 0
		nel = {}
		for x in self.species:
		    p = os.popen('grep "Z valence" '+self.psppath+'/'+x[0]+'.UPF','r')
		    nel[x[0]] = int(round(float(p.readline().split()[-3])))
		    p.close()
		for x in self.spos:
		    n += nel[x[0]]
		if not self.spinpol:
		    n /= 2
		print >>f, '  nbnd='+str(n-self.nbands)+','
	print >>f, '  occupations=\'smearing\','
	print >>f, '  smearing=\''+self.smearing+'\','
	print >>f, '  degauss='+str(self.sigma/rydberg)+'d0,'
	if self.spinpol:
	    print >>f, '  nspin=2,'
	    for i,m in enumerate(self.mkeys):
		print >>f, '  starting_magnetization(%d)=%sd0,' % (i+1,m)
	print >>f, '  input_dft=\''+self.xc+'\','
	edir = 3
	if dipfield:
	    try:
		edir = self.dipole['edir']
	    except:
		pass
	elif efield:
	    try:
		edir = self.field['edir']
	    except:
		pass
	if dipfield or efield:
	    print >>f, '  edir='+str(edir)+','
	if dipfield:
	    if self.dipole.has_key('emaxpos'):
		emaxpos = self.dipole['emaxpos']
	    else:
		emaxpos = 0.05
	    if self.dipole.has_key('eopreg'):
		eopreg = self.dipole['eopreg']
	    else:
		eopreg = 0.025
	    if self.dipole.has_key('eamp'):
		eamp = self.dipole['eamp']
	    else:
		eamp = 0.0
	    print >>f, '  emaxpos='+str(emaxpos)+'d0,'
	    print >>f, '  eopreg='+str(eopreg)+'d0,'
	    print >>f, '  eamp='+str(eamp)+'d0,'
	if efield:
	    if self.field.has_key('emaxpos'):
		emaxpos = self.field['emaxpos']
	    else:
		emaxpos = 0.0
	    if self.field.has_key('eopreg'):
		eopreg = self.field['eopreg']
	    else:
		eopreg = 0.0
	    if self.field.has_key('eamp'):
		eamp = self.field['eamp']
	    else:
		eamp = 0.0
	    print >>f, '  emaxpos2='+str(emaxpos)+'d0,'
	    print >>f, '  eopreg2='+str(eopreg)+'d0,'
	    print >>f, '  eamp2='+str(eamp)+'d0,'
	if self.U is not None:
	    print >>f, '  lda_plus_u=.true.,'
	    if self.J is not None:
		print >>f, '  lda_plus_u_kind=1,'
	    else:
		print >>f, '  lda_plus_u_kind=0,'
	    for i,s in enumerate(self.species):
		if self.U.has_key(s[0]):
		    print >>f, '  Hubbard_U('+str(i+1)+')='+str(self.U[s[0]])+'d0,'
	    if self.J is not None:
		for i,s in enumerate(self.species):
		    if self.J.has_key(s[0]):
			print >>f, '  Hubbard_J(1,'+str(i+1)+')='+str(self.J[s[0]])+'d0,'

	print >>f,'/\n&ELECTRONS'
	try:
	    diag = self.convergence['diag']
	    print >>f,'  diagonalization=\''+diag+'\','
	except:
	    pass
	if self.convergence is None:
	    print >>f, '  conv_thr='+str(5e-6/rydberg)+'d0,'
	else:
	    if self.convergence.has_key('energy'):
		print >>f, '  conv_thr='+str(self.convergence['energy']/rydberg)+','
	    else:
		print >>f, '  conv_thr='+str(5e-6/rydberg)+','
	for x in self.convergence.keys():
	    if x=='mixing':
		print >>f, '  mixing_beta='+str(self.convergence[x])+'d0,'
	    elif x=='maxsteps':
		print >>f, '  electron_maxstep='+str(self.convergence[x])+','
	    elif x=='nmix':
		print >>f, '  mixing_ndim='+str(self.convergence[x])+','
	    elif x=='mixing_mode':
		print >>f, '  mixing_mode=\''+self.convergence[x]+'\','

	print >>f, '/\n&IONS\n  ion_dynamics=\'ase3\',\n/'

	print >>f, 'CELL_PARAMETERS'
	for i in range(3):
	    print >>f, '%21.15fd0 %21.15fd0 %21.15fd0' % (self.atoms.cell[i][0],self.atoms.cell[i][1],self.atoms.cell[i][2])

	print >>f, 'ATOMIC_SPECIES'
	if self.spinpol:
	    for k in self.mkeys:
		x = self.msym[k]
		print >>f, x[0], x[1], x[0].split('_')[0]+'.UPF'
	else:
	    for x in self.species:
		print >>f, x[0], x[1], x[0]+'.UPF'
	
	print >>f, 'ATOMIC_POSITIONS {crystal}'
	if self.spinpol:
	    for m,x in zip(self.smag,self.spos):
		print >>f, '%-8s %21.15fd0 %21.15fd0 %21.15fd0' % (self.msym[m][0],x[1][0],x[1][1],x[1][2])
	else:
	    for x in self.spos:
		print >>f, '%-2s %21.15fd0 %21.15fd0 %21.15fd0' % (x[0],x[1][0],x[1][1],x[1][2])
	
	print >>f, 'K_POINTS automatic'
	print >>f, self.kpts[0], self.kpts[1], self.kpts[2], '0 0 0'
	f.close()
	
    def set_atoms(self, atoms):
	if self.atoms is None:
	    self.atoms = atoms.copy()
	else:
	    msg = 'creation of new QE calculator object required for new atoms'
	    if len(atoms)!=len(self.atoms):
		raise ValueError, msg
	    x = atoms.cell-self.atoms.cell
	    if max(x.flat)>1E-13 or min(x.flat)<-1E-13 or \
		atoms.get_atomic_numbers()!=self.atoms.get_atomic_numbers():
		raise ValueError, msg
	self.atoms = atoms.copy()

    def update(self, atoms):
	if self.atoms is None:
	    self.set_atoms(atoms)
	x = atoms.positions-self.atoms.positions
	if max(x.flat)>1E-13 or min(x.flat)<-1E-13 or not self.started:
	    self.recalculate = True
	    self.read(atoms)
	self.atoms = atoms.copy()

    def get_name(self):
	return 'QE-ASE3 interface'

    def get_version(self):
	return '0.1'

    def get_stress(self, atoms):
	raise NotImplementedError, 'stress interface not implemented\ntry using QE\'s internal relaxation routines instead'

    def read(self, atoms):
	if not self.started:
	    fresh = True
	    self.initialize(atoms)
	else:
	    fresh = False
	if self.recalculate:
	    if not fresh:
		p = atoms.positions
		print >>self.cinp, 'G'
		for x in p:
		    print >>self.cinp, ('%.15e %.15e %.15e' % (x[0],x[1],x[2])).replace('e','d')
		self.cinp.flush()
	    s = open(self.localtmp+'/log','a')
	    a = self.cout.readline()
	    s.write(a)
	    while a!='' and a[:17]!='!    total energy' and a[:13]!='     stopping':
		a = self.cout.readline()
		s.write(a)
		s.flush()
	    if a[:13]=='     stopping':
		raise RuntimeError, 'SCF calculation failed'
            elif a=='':
                raise RuntimeError, 'SCF calculation didn\'t converge'
	    self.energy_free = float(a.split()[-2])*rydberg
#	    a = self.cout.readline()
#	    s.write(a)
#	    while a[:13]!='     smearing':
#		a = self.cout.readline()
#		sys.stdout.flush()
#		s.write(a)
#	    self.energy_zero = self.energy_free - float(a.split()[-2])*rydberg
	    self.energy_zero = self.energy_free
	    a = self.cout.readline()
	    s.write(a)
	    while a[:5]!=' !ASE':
		a = self.cout.readline()
		s.write(a)
	    if not hasattr(self, 'forces'):
		self.forces = np.empty((self.natoms,3), np.float)
	    for i in range(self.natoms):
		self.cout.readline()
	    for i in range(self.natoms):
		self.forces[i][:] = [float(x) for x in self.cout.readline().split()]
	    self.forces *= rydberg_over_bohr
	    self.recalculate = False
	    s.close()
		

    def initialize(self, atoms):
	if not self.started:
	    a = self.atoms
	    s = a.get_chemical_symbols()
	    m = a.get_masses()
	    sd = {}
	    for x in zip(s, m):
		sd[x[0]] = x[1]
	    k = sd.keys()
	    k.sort()
	    self.species = [(x,sd[x]) for x in k]
	    self.nspec = len(self.species)
	    self.natoms = len(s)
	    self.spos = zip(s, a.get_scaled_positions())
	    self.writeinputfile()
	    self.start()
    
    def start(self):
	if not self.started:
	    if self.batch:
		cdir = os.getcwd()
		os.chdir(self.localtmp)
		self.cinp, self.cout = os.popen2(perProcMpiExec+' -wdir '+self.scratch+' pw.x -in '+self.localtmp+'/pw.inp')
		os.chdir(cdir)
	    else:
		self.cinp, self.cout = os.popen2('cd '+self.scratch+' ; '+'pw.x -in '+self.localtmp+'/pw.inp')
	    self.started = True

    def stop(self):
	if self.started:
	    print >>self.cinp, 'Q'
	    self.cinp.flush()
	    s = open(self.localtmp+'/log','a')
	    a = self.cout.readline()
	    s.write(a)
	    while a!='':
		a = self.cout.readline()
		s.write(a)
	    s.close()
	    self.cinp.close()
	    self.cout.close()
	    self.started = False

    def write_pot(self, filename='pot.xsf'):
	if filename[0]!='/':
	    file = self.localtmp+'/'+filename
	else:
	    file = filename
	self.update(self.atoms)
	self.stop()
	f = open(self.localtmp+'/pp.inp', 'w')
	print >>f, '&inputPP\n  prefix=\'calc\'\n  outdir=\'.\','
	print >>f, '  plot_num=11,\n  filplot=\''+file+'\'\n/\n'
	print >>f, '&plot\n  iflag=3,\n  outputformat=3\n/'
	f.close()
	if self.batch:
	    cdir = os.getcwd()
	    os.chdir(self.localtmp)
	    os.system(perProcMpiExec+' -wdir '+self.scratch+' pp.x -in '+self.localtmp+'/pp.inp >>'+self.localtmp+'/pp.log')
	    os.chdir(cdir)
	else:
	    os.system('cd '+self.scratch+' ; '+'pp.x -in '+self.localtmp+'/pp.inp >>'+self.localtmp+'/pp.log')
