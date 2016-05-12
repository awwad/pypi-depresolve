"""
<Program Name>
  rbtpip_integrate.py

<Purpose>
  A script to test rbtcollins' backtracking resolver patch for pip, using
  some depresolve code to do analysis.
"""

import subprocess # shell commands to create and use virtual environments
import random, string # randomized naming for virtual environments
import sys # for arguments to main
import json
import depresolve
import depresolve.deptools as deptools
import depresolve.depdata as depdata
import depresolve.resolver.resolvability as ry
import depresolve._external.timeout as timeout
#import depresolve.scrape_deps_and_detect_conflicts as scraper

SOLUTIONS_JSON_FNAME = 'data/resolved_via_rbtpip.json'
VENV_CATALOG_JSON_FNAME = 'data/rbtpip_venv_catalog.json'
ELABORATED_DEPS_JSON_FNAME = 'data/elaborated_dependencies.json'
# This one shouldn't be used directly, but it's faster than
# depdata.ensure_...
CONFLICTS_3_JSON_FNAME = 'data/conflicts_3.json'



def rbttest(distkey, edeps, versions, local=False,
    dir_rbt_pip='../pipcollins'):
  """

  Accepts a distkey indicating what distribution to try to install using
  rbtcollins' issue-988 pip branch as a way to solve conflicts.

  Steps:
    1. Solve using rbtcollins' pip branch issue-988:
       For each distkey, create a new virtual environment, install rbtcollins'
       pip version within it, use it to install the dist indicated, and use
       `pip list` to determine what it installed.

    2. Run resolvability.are_fully_satisfied to test the solution set for
       consistency.


  Arguments:
    - distkeys: a list of distkeys indicating what distributions to solve for
    - edeps: elaborated dependency data (see depdata.py)
    - versions: versions by package (dict mapping package name to the available
      versions for that package)
    - local (optional):
        - if not provided, we connect to PyPI
        - if simply set to 'True', we use the default local bandersnatch
          location for the simple listing of packages,
          'file:///srv/pypi/web/simple'.
        - if another value is provided, we interpret it as a string indicating
          the location of the simple index listing of packages on the mirror
          to use.

  Returns:
    - Installed: True if distkey was able to be installed with rbt pip
    - Satisfied: True if the solution set rbt pip generated was fully
      satisfied, i.e. all dependencies of the given dist were satisfied, along
      with all of their dependencies, and so on, with no dependency conflicts.
    - Solution: the solution set (all distkeys installed)
    - errstring: a string describing the error encountered if any was
      encountered.

  """


  logger = depresolve.logging.getLogger('rbtpip_integrate.rbttest')

  errstring = ''

  ###############
  # Step 1
  # Figure out the install candidate solution.
  logger.info('Starting rbt resolve of ' + distkey)

  solution = [] # try-scoping paranoia

  # Run rbtcollins' pip branch to find the solution, with some acrobatics.
  try:
    solution = rbt_backtracking_satisfy(distkey, edeps, versions, local)

  except depresolve._external.timeout.TimeoutException as e:
    errstring = 'Timed out during install'
    logger.error('Unable to install ' + distkey + ' using rbt pip. ' +
        errstring)
    return (False, False, [], errstring)



  ###############
  # Step 2: Run resolvability.are_fully_satisfied to test the solution set
  # for consistency.

  # Test the solution.
  # If the given solution doesn't even include the distribution to install
  # itself, it's obviously not been successful.

  satisfied = False
  installed = distkey in [d.lower() for d in solution] # sanitize old data

  if not installed:
    if solution:
      errstring = 'Non-empty solution without target distkey'
      logger.error('Unable to install ' + distkey + ' using rbt pip. ' + 
          errstring + '. Solution does not contain ' + distkey + '. Presume '
          'failure; unclear why anything was installed at all - possibly '
          'failure in middle of installations, after some dependencies were '
          'installed? Solution was: ' + str(solution))
    
    else:
      errstring = 'Empty solution'
      logger.error('Unable to install ' + distkey + ' using rbt pip. ' +
          errstring + '. Presume pip failure.')

  else:
    # If it's in there, then we check to see if the solution is fully
    # satisfied. (Note that because virtual environments start off with pip,
    # wheel, and setuptools, we can't tell when a solution includes them,
    # don't store those as part of the solution, and so disregard them in this
    # dependency check. ):
    try:
      satisfied = ry.are_fully_satisfied(solution, edeps, versions,
          disregard_setuptools=True)
    except depresolve.MissingDependencyInfoError as e:
      errstring = 'Unable to determine if satisfied: missing dep info for ' + \
          str(e.args[1])
      satisfied = ''   # evaluates False but is not False
      logger.error(errstring + '. Resolution for ' + distkey + ' unknown. ' +
          ' Full exception:' + str(e))


  # Load venv catalog to print debug info. Clunky.
  venv_catalog = depdata.load_json_db(VENV_CATALOG_JSON_FNAME)

  logger.info('Tried solving ' + distkey + ' using rbtcollins pip patch. '
      'Installed: ' + str(installed) + '. Satisfied: ' + str(satisfied) +
      ' virtualenv used: ' + venv_catalog[distkey])

  # Return the solution that rbt generates for this distkey:
  #  - whether or not the distkey itself was installed
  #  - whether or not the install set is fully satisfied and conflict-less
  #  - what the solution set is
  #  - error string if there was an error
  return (installed, satisfied, solution, errstring)





def rbt_backtracking_satisfy(distkey, edeps, versions_by_package, local=False,
    dir_rbt_pip='../pipcollins'):
  """
  Determine correct install candidates by using rbtcollins' pip branch
  issue-988.

  Steps:
    1. Sets up a random-name new virtual environment
    2. Installs rbtcollins' pip patch on that virtual environment
    3. Installs the given distribution using rbt pip
    4. Runs `pip list` and harvests the solution set

  Args & output modeled after resolver.resolvability.backtracking_satisfy().

  Additional, optional argument:
   - local (optional):
        - if not provided, we connect to PyPI
        - if simply set to 'True', we use the default local bandersnatch
          location for the simple listing of packages,
          'file:///srv/pypi/web/simple'.
        - if another value is provided, we interpret it as a string indicating
          the location of the simple index listing of packages on the mirror
          to use.


  """

  logger = depresolve.logging.getLogger(
      'rbtpip_integrate.rbt_backtracking_satisfy')

  assert distkey == distkey.lower(), 'distkeys should always be lowercase!' + \
      distkey + ' is not!'  # Remember not to use distkey.islower(). Bug.

  ###############
  # Steps 1 and 2: Create venv and install rbt pip.
  venv_name = 'v3_'
  for i in range(0,5):
    venv_name += random.choice(string.ascii_lowercase + string.digits)

  # Save a map of this virtual environment name to distkey for later auditing
  # if interesting things happen.
  venv_catalog = depdata.load_json_db(VENV_CATALOG_JSON_FNAME)
  venv_catalog[distkey] = venv_name
  json.dump(venv_catalog, open(VENV_CATALOG_JSON_FNAME, 'w'))


  cmd_venvcreate = 'virtualenv -p python3 --no-site-packages ' + venv_name
  cmd_sourcevenv = 'source ' + venv_name + '/bin/activate'
  cmd_piplist = cmd_sourcevenv + '; pip list --disable-pip-version-check'
  cmd_install_rbt_pip = cmd_sourcevenv + '; cd ' + dir_rbt_pip + \
      '; pip install -e . --disable-pip-version-check'
  #cmd_check_pip_ver = cmd_sourcevenv + '; pip --version'
  #cmd_install_seb_pip = cmd_sourcevenv + '; cd ' + dir_seb_pip + '; pip install -e .'
  #cmd_install_depresolve = cmd_sourcevenv + '; cd ' + dir_depresolve + '; pip install -e .'

  # Create venv
  logger.info('For ' + distkey + ', creating virtual environment ' + venv_name)
  popen_wrapper(cmd_venvcreate)

  # Initial snapshot of installed packages
  popen_wrapper(cmd_piplist)

  # Install rbtcollins' issue_988 pip branch and display pip version
  # (should then be 8.0.0dev0)
  logger.info('For ' + distkey + ', installing rbt_pip in ' + venv_name)
  popen_wrapper(cmd_install_rbt_pip)
  #popen_wrapper(cmd_check_pip_ver)



  ###############
  # Step 3: Install given dist using rbt pip.

  # Deconstruct distkey into package and version for pip.
  packname = depdata.get_packname(distkey)
  version_string = depdata.get_version(distkey)
  # Construct as a requirement for pip install command.
  requirement = packname + '==' + version_string


  # Put together the pip command.

  # First, are we using PyPI or a specified (local) mirror?
  index_optional_args = ''
  if local == True: # If local is just the value True, use default local mirror
    index_optional_args = '-i file:///srv/pypi/web/simple'

  elif local: # If local is a specific string, assume it's the index location.
    index_optional_args = '-i ' + local

  else:
    pass # Proceed normally, using PyPI, not adding index arguments.

  # Would love to be able to just call
  # scraper._call_pip_with_timeout(pip_arglist), but can't because we have to
  # do this in a virtual environment, so doing it this way instead:
  cmd_install_dist = cmd_sourcevenv + \
      '; pip install --disable-pip-version-check --quiet ' + \
      index_optional_args + ' ' + requirement

  logger.info('For ' + distkey + ', using rbtpip to install in ' + venv_name)
  popen_wrapper(cmd_install_dist) # have incorporated 5 min timeout




  ###############
  # Step 4: Run `pip list` and harvest the solution set
  # Initial snapshot of installed packages
  piplist_output = subprocess.Popen(cmd_piplist, shell=True,
      executable='/bin/bash', stdout=subprocess.PIPE).stdout.readlines()

  # Now I have to parse out the actual installs from the output... /:
  # It looks like this, for example, in python3:
  #[b'cffi==1.5.0\n',
  # b'cryptography==1.2.2\n',
  # b'idna==2.0\n',
  # b'iso8601==0.1.11\n',
  # b'pyasn1==0.1.9\n',
  # b'pycparser==2.14\n',
  # b'pycrypto==2.6.1\n',
  # b'PyNaCl==1.0.1\n',
  # b'six==1.10.0\n',
  # b'tuf==0.10.0\n',
  # b'wheel==0.26.0\n']

  solution = []

  # Convert list_output into solution set here.

  # Yeah, this isn't really kosher, and it probably breaks in python2.
  # Principle for this week: first, get it to work.
  for line in piplist_output:

    installed_distkey = line.decode()[:-1] # decode and cut off \n at end (assumption: actual newline)

    # pip list outputs almost-distkeys, like: 'pbr (0.11.1)'.
    # We cut out the space, lowercase, and pray they work. /:
    installed_distkey = installed_distkey.replace(' ', '').lower()

    ## Old way, using `pip freeze`.
    ## # Split it into package name and version:
    ## (name, ver) = installed_package.split('==')
    ##
    ## # Put it together into a distkey.
    ## installed_distkey = depdata.distkey_format(name, ver)


    # These distributions are installed when a new virtual environment is
    # created, so ignore them. This is an unpleasant hack: some packages
    # actually declare dependencies on these, and so the stored solutions may
    # be incomplete, and there's a hack in is_dep_satisfied to disregard these
    # when given disregard_setuptools=True.
    if installed_distkey.startswith('wheel(') or \
        installed_distkey.startswith('pip(') or \
        installed_distkey.startswith('setuptools('):
      continue

    solution.append(installed_distkey)

  return solution





@timeout.timeout(300) # Timeout after 5 minutes.
def popen_wrapper(cmd):
  """
  Just runs subprocess.popen with the given command and prints the output to
  the screen. Times out after 5 minutes.
  """
  print(subprocess.Popen(cmd, shell=True, executable='/bin/bash',
      stdout=subprocess.PIPE).stdout.readlines())





def main():
  """
  Choose some conflicting dists to test rbtcollins solver on.

    Steps:
    1. Load dependency data.

    2. Call rbttest to solve using rbtcollins' pip branch issue-988 and
       determine the correctness of that solution.

    3. Write all the solution sets and correctness info to a json file.

  """

  logger = depresolve.logging.getLogger('rbtpip_integrate.main')


  distkeys_to_solve = []

  n_distkeys = 3 # default 3, overriden by argument --n=, or specific distkeys

  args = sys.argv[1:]
  local = False
  all_conflicting = False

  if args:
    for arg in args:
      if arg == '--local':
        local = True
      elif arg.startswith('--local='):
        local = arg[8:]
      elif arg.startswith('--n='):
        n_distkeys = int(arg[4:])
      elif arg == '--all':
        local = True
        all_conflicting = True
      else:
        distkeys_to_solve.append(arg)

  # If we didn't get any specific distkeys to solve for from the args, then
  # pick randomly:

  if distkeys_to_solve:
    n_distkeys = len(distkeys_to_solve)

  if not distkeys_to_solve:
    # Randomize from the model 3 conflict list.
    
    depdata.ensure_data_loaded()

    con3 = depdata.conflicts_3_db

    conflicting = [p for p in con3 if con3[p]]

    if all_conflicting:
      distkeys_to_solve = conflicting

    else:
      import random
      for i in range(0, n_distkeys):
        distkeys_to_solve.append(random.choice(conflicting))



  ###############
  # Step 1: Load dependency data.

  # Load dependencies from their json file, harvested in prior full run of
  # scraper.
  depdata.ensure_data_loaded()
  deps = depdata.dependencies_by_dist

  # Make catalog of versions by package from deps info.
  versions = deptools.generate_dict_versions_by_package(deps)

  # Elaborate the dependencies using information about available versions.
  #edeps = deptools.elaborate_dependencies(deps, versions)
  # EDIT: this is very time consuming and we may be dealing with the full
  # dependency data, so instead I'm going to load already-elaborated
  # dependencies. NOTE THAT THIS IS NOT AUTOMATICALLY REFRESHED AND SO IF THERE
  # IS MORE DEPENDENCY DATA ADDED, ELABORATION SHOULD BE DONE OVER. (The full
  # data set may take 30 minutes to elaborate!)
  edeps = depdata.load_json_db(ELABORATED_DEPS_JSON_FNAME) # potentially STALE!


  # Prepare solution dictionary.
  solution_dict = depdata.load_json_db(SOLUTIONS_JSON_FNAME)



  ###############
  # Step 2: Run rbttest to solve and test solution.
  for distkey in distkeys_to_solve:

    if distkey in solution_dict:
      logger.info('Skipping rbt solve for ' + distkey + ' (already have '
          'results).')
      continue

    logger.info('Starting rbt solve for ' + distkey)
    # Explicit with multiple variables for clarity for the reader.
    (installed, satisfied, solution, errstring) = \
        rbttest(distkey, edeps, versions, local)
    solution_dict[distkey] = (installed, satisfied, solution, errstring)

    ###############
    # Step 3: Dump solutions and solution correctness info to file.
    # Until this is stable, write after every solution so as not to lose data.
    logger.info('Writing results for ' + distkey)
    json.dump(solution_dict, open(SOLUTIONS_JSON_FNAME, 'w'))







if __name__ == '__main__':
  main()
