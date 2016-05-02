## VcliSublime
VcliSublime adds support in Sublime Text 3 to connect with [Vertica](https://github.com/vertica) and run queries with smart autocompletion. It is based on [PgcliSublime](https://github.com/darikg/PgcliSublime)


## Installation
This plugin requires following packages for proper execution:

* [vcli](https://github.com/dbcli/vcli) with Python 3 support
* [vertica_python](https://github.com/uber/vertica-python) with Python 3 support

First, clone this repository in Sublime Text Packages directory which in Linux usually located at ~/.config/sublime-text-3/Packages
```git
git clone https://github.com/adnanyaqoobvirk/VcliSublime.git
```

Second, create a virtual environment based on Python 3 in directory of your liking using the following command
```bash
virtualenv -p python3 vcli
```

Next you need to install **vcli** and **vertica_python** in the freshly created virtual environment using pip, currently these packages do not support Python 3, I have made changes in them to support the Python 3 so you need to install these packages from my git repositories:
```bash
source vcli/bin/activate
pip install https://github.com/adnanyaqoobvirk/vertica_python.git
pip install https://github.com/adnanyaqoobvirk/vcli.git
```

##Configuration

There are two important configurations for proper functioning of this plugin:

First, ```"vcli_site_dirs"``` need to be set to site-packages directory of virtual environment you created while installing this plugin. Second, Vertica connection url need to be set in ```"vcli_url"``` variable. For example:

```json
{
    "vcli_site_dirs": ["/home/adnan/Desktop/virts/vcli/lib/python3.3/site-packages"],
    "vcli_url": "vertica://user:password@192.168.1.1:5433/test_db"
}
```

**Important Note:** Because site-packages path is added to the global scope of Sublime Text, it is prone to errors. So, you should make sure that if other paths are added to the global scope then they don't have conflicting packages. For example, If you are also using PgcliSublime then the site-packages directory of PgcliSublime virtual environment will also be in the global scope so package versions may have conflicts. One conflict, I faced was that pgcli default pip package have old versions of dependencies. These dependencies are also the dependencies of vcli and this result in package version clash. The solution is to install pgcli directly from GitHub repository instead of pip repository while installing PgcliSublime.

###Other Configurations
All other configurations are same as PgcliSublime configurations. Only change is the name of variables.

**Keyboard Shortcuts** are set to different so that they don't have clashes with PgcliSublime shortcuts.

##Special Thanks
Special thanks go to [darikg](https://github.com/darikg) for creating the PgcliSublime plugin.
